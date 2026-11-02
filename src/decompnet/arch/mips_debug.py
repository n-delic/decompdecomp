"""Exact procedure extents from MIPS ECOFF debug records, including IDO private helpers.

IDO's optimizer passes private arguments in a whole unit's chosen registers. Its
assembler omits those procedures from .symtab but retains their names/start/end in
.mdebug. Read that metadata; never infer a helper boundary from target bytes.
Layout: binutils include/coff/sym.h (HDRR, FDR and SYMR).
"""
import struct


def records(elf):
    """name -> (executable section, offset, explicit length); malformed/ambiguous records give no evidence."""
    cached=getattr(elf,'_decomp_ecoff_functions',None)
    if cached is not None:return cached
    result={}
    try:result=_read(elf)
    except (ValueError,KeyError,IndexError,struct.error,UnicodeError,OverflowError):pass
    elf._decomp_ecoff_functions=result
    return result


def placeholders(elf):
    """Explicit unused inline-return stubs; neither hidden functions nor arbitrary padding."""
    cached=getattr(elf,'_decomp_ecoff_placeholders',None)
    if cached is not None:return cached
    found=records(elf)
    if not found:return {}
    symtab=elf.get_section_by_name('.symtab')
    ordinary={s.name for s in symtab.iter_symbols()
              if isinstance(s['st_shndx'],int) and s['st_shndx']>0} if symtab else set()
    candidates={n:v for n,v in found.items() if n not in ordinary and v[2]==8
                and elf.get_section(v[0]).data()[v[1]:v[1]+8]==bytes.fromhex('03e0000800000000')}
    if not candidates:
        elf._decomp_ecoff_placeholders={}
        return {}
    referenced=set();opaque=False;order='little' if elf.little_endian else 'big'
    for section in elf.iter_sections():
        if section['sh_type'] not in ('SHT_REL','SHT_RELA'):continue
        use=elf.get_section(section['sh_info'])
        if not use['sh_flags']&2:continue  # debug/unwind references do not keep an inline body alive
        symbols=elf.get_section(section['sh_link'])
        for rel in section.iter_relocations():
            symbol=symbols.get_symbol(rel['r_info_sym'])
            if symbol['st_shndx'] not in {v[0] for v in candidates.values()}:continue
            if symbol['st_info']['type']!='STT_SECTION':referenced.add((symbol['st_shndx'],symbol['st_value']));continue
            if rel.is_RELA():value=rel['r_addend']
            else:
                at=rel['r_offset'];value=int.from_bytes(use.data()[at:at+4],order)
                if rel['r_info_type']==4:value=(value&0x3ffffff)*4
            if rel['r_info_type'] not in (2,4):opaque=True;break
            referenced.add((symbol['st_shndx'],value))
    ignored={} if opaque else {n:v for n,v in candidates.items() if not any(sx==v[0] and v[1]<=at<v[1]+v[2] for sx,at in referenced)}
    elf._decomp_ecoff_placeholders=ignored
    return ignored


def functions(elf):
    return {n:v for n,v in records(elf).items() if n not in placeholders(elf)}


def _read(elf):
    section=elf.get_section_by_name('.mdebug')
    if section is None or section['sh_size']<96:return {}
    executable=[(i,s) for i,s in enumerate(elf.iter_sections()) if s['sh_flags']&4 and s['sh_size']]
    if len(executable)!=1:return {}
    sx,text=executable[0]
    stream=elf.stream;position=stream.tell()
    try:stream.seek(0);data=stream.read()
    finally:stream.seek(position)
    lo=section['sh_offset'];hi=lo+section['sh_size'];order='<' if elf.little_endian else '>'
    if hi>len(data):return {}
    def table(at,count,stride):
        if not lo<=at<=hi or not 0<=count<=65536 or at+count*stride>hi:raise ValueError('Invalid ECOFF table extent')
    header=struct.unpack_from(order+'HH23I',data,lo)
    if header[0]!=0x7009:return {}
    symbol_count,symbol_at=header[9:11];strings_size,strings_at=header[15:17];files,files_at=header[19:21]
    table(symbol_at,symbol_count,12);table(strings_at,strings_size,1);table(files_at,files,72)
    result={}
    for i in range(files):
        fd=struct.unpack_from(order+'10I2H7I',data,files_at+i*72)
        string_base,string_count,symbol_base,count=fd[2:6]
        if string_base+string_count>strings_size or symbol_base+count>symbol_count:raise ValueError('Invalid ECOFF file extent')
        starts={}
        for k in range(count):
            name_offset,value,bits=struct.unpack_from(order+'3I',data,symbol_at+(symbol_base+k)*12)
            if name_offset>=string_count:raise ValueError('Invalid ECOFF string offset')
            at=strings_at+string_base+name_offset;end=data.find(b'\0',at,strings_at+string_base+string_count)
            if end<at:raise ValueError('Unterminated ECOFF name')
            if elf.little_endian:kind,storage,index=bits&63,(bits>>6)&31,bits>>12
            else:kind,storage,index=bits>>26,(bits>>21)&31,bits&0xfffff
            if storage!=1 or (kind not in (6,14) and not (kind==8 and index in starts)):continue
            name=data[at:end].decode('utf-8')
            if kind in (6,14):starts[k]=(name,value)  # stProc/stStaticProc
            elif kind==8 and index in starts:  # stEnd points back to its procedure symbol
                original,start=starts.pop(index)
                if not name or name!=original or not value or (start|value)&3 or start+value>text['sh_size']:
                    raise ValueError('Invalid ECOFF procedure extent')
                entry=(sx,start,value)
                if name in result and result[name]!=entry:raise ValueError('Ambiguous ECOFF procedure name')
                result[name]=entry
        if starts:raise ValueError('Missing ECOFF procedure end')
    spans=sorted(result.values())
    if any(a[1]+a[2]>b[1] for a,b in zip(spans,spans[1:])):raise ValueError('Overlapping ECOFF procedures')
    symtab=elf.get_section_by_name('.symtab')
    if symtab:
        for symbol in symtab.iter_symbols():
            if symbol.name in result and symbol['st_info']['type']=='STT_FUNC':
                sx,start,size=result[symbol.name]
                if (symbol['st_shndx'],symbol['st_value'])!=(sx,start) or symbol['st_size'] not in (0,size):
                    raise ValueError('ECOFF disagrees with ELF function symbol')
    return result
