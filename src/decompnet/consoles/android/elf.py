"""Read ARM ELF function boundaries from sized symbols (#133).

No prologue guesses: a stripped library without usable function symbols is refused for now.
Names at one address are aliases, not extra functions. The low bit of each function symbol identifies ARM/Thumb mode.
"""

from __future__ import annotations

import io
import re

import capstone
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from ...rom import RomError
from ...rom.split import DataSymbol, Segment, SplitResult
from .split import Function

IDENT = re.compile(r"[A-Za-z_]\w*\Z", re.ASCII)


def image(data):
    try:
        elf = ELFFile(io.BytesIO(data))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM' or elf['e_type'] != 'ET_DYN':
            raise RomError("Choose a little-endian ARM32 shared ELF from an Android APK.")
        return elf
    except (ELFError, ValueError) as error:
        raise RomError(f"Couldn't read the Android ELF: {error}. Try extracting the library again.") from None


def symbols(elf):
    return [s for sec in elf.iter_sections() if isinstance(sec, SymbolTableSection) for s in sec.iter_symbols()
            if isinstance(s['st_shndx'], int) and s['st_shndx'] != 0]


def split(data, workdir, log=print):
    elf = image(data)
    functions, named, objects = {}, {}, {}
    sections = {i: sec for i, sec in enumerate(elf.iter_sections())}
    payloads = {i: sec.data() for i, sec in sections.items() if sec["sh_flags"] & 4}
    for sym in symbols(elf):
        value, size, name = sym['st_value'], sym['st_size'], sym.name
        if not size:
            continue
        section = sections[sym['st_shndx']]
        if sym['st_info']['type'] == 'STT_FUNC' and section['sh_flags'] & 4:
            addr = value & ~1
            offset = addr - section['sh_addr']
            if offset < 0 or offset + size > section['sh_size']:
                continue
            safe = name if IDENT.fullmatch(name) else f'sub_{addr:08X}'
            fn = Function(safe, addr, section['sh_offset'] + offset,
                          payloads[sym['st_shndx']][offset:offset + size], bool(value & 1))
            # Prefer a usable public name, with one function at an aliased address.
            if addr not in functions or functions[addr].name.startswith('sub_'):
                functions[addr] = fn
        elif sym['st_info']['type'] == 'STT_OBJECT' and section['sh_flags'] & 2:
            safe = name if IDENT.fullmatch(name) else f'D_{value:08X}'
            objects[value] = DataSymbol(safe, value, None if section['sh_type'] == 'SHT_NOBITS'
                                       else section['sh_offset'] + value - section['sh_addr'], size,
                                       'bss' if section['sh_type'] == 'SHT_NOBITS' else 'data')
    if not functions:
        raise RomError("This Android library has no sized function symbols. Analysis of fully stripped libraries isn't available yet; try another library or an unstripped build.")
    ordered = sorted(functions.values(), key=lambda f: f.vram)
    # Overlapping symbols can describe a thunk within a method. Keep the outer extent once; never double-count bytes.
    clean, end, used = [], -1, set()
    for fn in ordered:
        if fn.vram < end:
            continue
        if fn.name in used:
            fn.name = f'sub_{fn.vram:08X}'
        used.add(fn.name)
        clean.append(fn)
        end = fn.vram + fn.size
    segments = [Segment(f'load{i}', seg['p_offset'], seg['p_offset'] + seg['p_filesz'], seg['p_vaddr'])
                for i, seg in enumerate(elf.iter_segments()) if seg['p_type'] == 'PT_LOAD' and seg['p_filesz']]
    for index, fn in enumerate(clean):
        if index % 1000 == 0:
            getattr(log, 'check_cancel', lambda: None)()
        if index % 5000 == 0:
            log(f'Reading native references: {index}/{len(clean)} functions')
        reader = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if fn.thumb else capstone.CS_MODE_ARM)
        reader.detail = True
        for ins in reader.disasm(fn.code, fn.vram):
            if ins.mnemonic in ('bl', 'blx') and ins.operands and ins.operands[0].type == capstone.arm.ARM_OP_IMM:
                fn.refs.append((ins.address - fn.vram, 10 if fn.thumb else 28, ins.operands[0].imm & ~1))
        named[fn.name] = fn.vram
    log(f'Android: {len(clean)} sized native functions; DEX and assets remain in your original package.')
    return SplitResult(clean, list(objects.values()), segments, named, workdir / 'native.json', '', '', 'android', 0, 'game.so')


def function_at(data, addr, limit, name):
    elf = image(data)
    for sym in symbols(elf):
        if sym['st_info']['type'] == 'STT_FUNC' and sym['st_value'] & ~1 == addr and sym['st_size']:
            sec = elf.get_section(sym['st_shndx'])
            offset = addr - sec['sh_addr']
            size = min(sym['st_size'], limit - addr)
            if offset >= 0 and size > 0 and offset + size <= sec['sh_size']:
                return Function(name, addr, sec['sh_offset'] + offset, sec.data()[offset:offset + size], bool(sym['st_value'] & 1))
    return None
