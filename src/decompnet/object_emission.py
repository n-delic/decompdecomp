"""Bounded inventory of emitted code/data, independent of any focal-function verdict.

Allocated constants can be legitimate. Listing them does not prove that the ROM's data
matches, and extra bodies still need the normal export/context verification.
"""
import io
import struct

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from . import coffobj

LIMIT = 12
METADATA = {'.reginfo', '.MIPS.abiflags', '.eh_frame', '.ARM.exidx', '.ARM.extab'}


def inspect(raw, focal=None):
    """Report named definitions and allocated sections, without source or raw bytes."""
    functions, data, metadata, helpers, code = [], [], [], [], []
    common = set()
    try:
        if raw.startswith(b'\x7fELF'):
            elf = ELFFile(io.BytesIO(raw))
            for section in elf.iter_sections():
                if isinstance(section, SymbolTableSection):
                    for symbol in section.iter_symbols():
                        name = symbol.name
                        if '__repair_aggregate_call_' in name:
                            helpers.append(name)
                        if symbol['st_shndx'] == 'SHN_COMMON' and name not in common:
                            common.add(name)
                            data.append(dict(name='COMMON:'+name,size=symbol['st_size']))
                        if symbol['st_shndx'] != 'SHN_UNDEF' and symbol['st_info']['type'] == 'STT_FUNC':
                            functions.append(dict(name=name, size=symbol['st_size']))
                if section['sh_flags'] & 2 and section['sh_flags'] & 4 and section['sh_size']:
                    code.append(dict(name=section.name,size=section['sh_size']))
                if section['sh_flags'] & 2 and not section['sh_flags'] & 4 and section['sh_size']:
                    (metadata if section.name in METADATA else data).append(dict(name=section.name,size=section['sh_size']))
            kind = 'ELF'
        elif coffobj.is_coff(raw):
            _, count, _, table, symbols, _, _ = struct.unpack_from(coffobj.HEADER, raw, 0)
            strings = table+coffobj.SYMBOL_BYTES*symbols
            if count < 1 or table < 20+count*40 or strings+4 > len(raw):
                raise ValueError('Truncated COFF tables')
            index = 0
            while index < symbols:
                name, offset, section, typ, storage, aux = struct.unpack_from('<8sIhHBB',raw,table+18*index)
                if name[:4] == bytes(4):
                    start = strings+struct.unpack_from('<I',name,4)[0]
                    name = raw[start:raw.index(b'\0',start)]
                raw_name = name.rstrip(b'\0').decode('latin1')
                name = coffobj.c_name(raw_name)
                if '__repair_aggregate_call_' in raw_name:
                    helpers.append(raw_name)
                if section == 0 and offset > 0 and storage == 2 and not typ & 0x20 and name not in common:
                    common.add(name)
                    data.append(dict(name='COMMON:'+name,size=offset))
                if typ & 0x20 and section > 0:
                    functions.append(dict(name=name,size=None))
                index += 1+aux
            for index in range(count):
                name, virtual_size, _, size, _, _, _, _, _, flags = struct.unpack_from('<8sIIIIIIHHI',raw,20+40*index)
                if flags & 0x80 and not size:
                    size = virtual_size
                if size and flags & 0x20000000:
                    code.append(dict(name=name.rstrip(b'\0').decode('latin1'),size=size))
                if size and flags & (0x40|0x80) and not flags & 0x20000000:
                    data.append(dict(name=name.rstrip(b'\0').decode('latin1'),size=size))
            kind = 'COFF'
        else:
            return dict(available=False,reason='Object emission inventory needs ELF or supported COFF.')
    except (ELFError, ValueError, TypeError, IndexError, KeyError, OSError, struct.error):
        return dict(available=False,reason='Object emission inventory could not read this object.')
    functions = list({f['name']:f for f in functions}.values())
    extra = [f for f in functions if focal is not None and f['name'] != focal]
    return dict(available=True,format=kind,functions=functions[:LIMIT],function_count=len(functions),functions_truncated=len(functions)>LIMIT,
                extra_functions=extra[:LIMIT],extra_function_count=len(extra),
                allocated_data=data[:LIMIT],allocated_data_sections=len(data),allocated_data_bytes=sum(d['size'] for d in data),
                metadata=metadata[:LIMIT],executable_sections=code[:LIMIT],executable_bytes=sum(s['size'] for s in code),
                remaining_generated_helpers=helpers[:LIMIT],
                note='Inventory only; constants may be required. Normal linked-data and full-image checks remain necessary.')


def text(report):
    if not report.get('available'):
        return report.get('reason','Object inventory unavailable.')
    functions = ', '.join(f['name']+(f" ({f['size']} bytes)" if f['size'] is not None else '') for f in report['functions'][:4]) or 'none'
    data = ', '.join(f"{d['name']} ({d['size']} bytes)" for d in report['allocated_data'][:4]) or 'none'
    code = ', '.join(f"{s['name']} ({s['size']} bytes)" for s in report['executable_sections'][:4]) or 'none'
    if report['function_count']>4:functions += ', …'
    if report['allocated_data_sections']>4:data += ', …'
    return f'Executable sections: {code}. Emitted functions ({report["function_count"]}): {functions}. Allocated data: {data}. '+report['note']
