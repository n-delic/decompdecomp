"""The original ARM shared ELF's GOT and PLT: evidence used by the judge, never guessed addresses (#133).

Dynamic relocations identify each GOT slot's symbol or local pointer. PLT instruction sequences identify the
slot a call stub uses. This works for renamed symbols through their address, and for unresolved imports by name.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO

import capstone
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection


@dataclass
class Image:
    program: bytes = b""
    segments: list = field(default_factory=list)
    got_base: int | None = None
    slots: dict = field(default_factory=dict)  # slot -> (original name, address or None for an external import)
    plt: dict = field(default_factory=lambda: defaultdict(list))
    values: dict = field(default_factory=dict)
    imports: set = field(default_factory=set)
    types: dict = field(default_factory=dict)
    functions: set = field(default_factory=set)
    veneers: set = field(default_factory=set)
    bss: list = field(default_factory=list)
    thumb: set = field(default_factory=set)
    plt_values: dict = field(default_factory=lambda: defaultdict(list))

    def read(self, address, size):
        for vram, offset, length in self.segments:
            if vram <= address and address + size <= vram + length:
                at = offset + address - vram
                return self.program[at:at + size]
        return None

    def through_veneer(self, address):
        """Only recognised linker veneers; their bytes, target and instruction modes are all checked."""
        from .arm import arm_b_dest

        thumb = bool(address & 1 or address in self.thumb)
        address &= ~1
        if address in self.functions and address not in self.veneers:
            return address  # a named game function must be called by its own symbol, even if it happens to be a thunk
        data = self.read(address, 16) or self.read(address, 12)
        if data is None:
            return address
        if thumb and len(data) >= 12:
            reader = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
            reader.detail = True
            ins = list(reader.disasm(data[:12], address))
            if (len(ins) == 4 and [i.mnemonic for i in ins] == ['movw', 'movt', 'add', 'bx']
                    and ins[0].op_str.startswith('ip, #') and ins[1].op_str.startswith('ip, #')
                    and ins[2].op_str == 'ip, pc' and ins[3].op_str == 'ip'):
                immediate = ins[0].operands[1].imm | ins[1].operands[1].imm << 16
                return ((ins[2].address + 4) & ~3) + immediate & 0xFFFFFFFE
        if data[:2] == bytes.fromhex('78 47') and data[2:4] in (bytes.fromhex('00 bf'), bytes.fromhex('c0 46')):
            dest = arm_b_dest(data, 4, address + 4)
            if dest is not None:
                return dest
            if len(data) >= 16 and data[4:12] == bytes.fromhex('00 c0 9f e5 0f f0 8c e0'):
                return (address + 16 + int.from_bytes(data[12:16], 'little')) & 0xFFFFFFFE
            return address
        if data[:4] == bytes.fromhex('04 f0 1f e5'):
            return int.from_bytes(data[4:8], 'little') & ~1
        if data[:8] == bytes.fromhex('00 c0 9f e5 1c ff 2f e1'):
            return int.from_bytes(data[8:12], 'little') & ~1
        if len(data) >= 16 and data[:12] == bytes.fromhex('04 c0 9f e5 0c c0 8f e0 1c ff 2f e1'):
            return (address + 12 + int.from_bytes(data[12:16], 'little')) & 0xFFFFFFFE
        return address

    def value(self, name, symbols):
        value = symbols.resolve(name)
        if value is not None:
            return value | (1 if value in self.thumb else 0)
        return self.values.get(name)

    def pointer(self, name, symbols, addend=0):
        value = self.value(name, symbols)
        if value is None:
            return None
        sym = symbols.by_name.get(name)
        function = self.types.get(name) == 'STT_FUNC' or sym is not None and sym.kind == 'func' or (value & ~1) in self.functions
        thumb = (value & 1) if function else 0
        return (((value & ~thumb) + addend) | thumb) & 0xFFFFFFFF

    def destinations(self, name, symbols):
        base = self.value(name, symbols)
        values = [] if base is None else [base]
        values += self.plt.get(name, [])
        if base is not None:
            values += self.plt_values.get(base, [])
        return values

    def slot_matches(self, slot, name, symbols):
        item = self.slots.get(slot)
        if item is None:
            return False
        original_name, value = item
        wanted = self.value(name, symbols)
        return value == wanted if value is not None and wanted is not None else original_name == name and value is None


@lru_cache(maxsize=3)
def image(program: bytes) -> Image:
    elf = ELFFile(BytesIO(program))
    result = Image(program=program)
    segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
    result.segments = [(s['p_vaddr'], s['p_offset'], s['p_filesz']) for s in segments]
    def word(address):
        for seg in segments:
            offset = address - seg['p_vaddr']
            if 0 <= offset <= seg['p_filesz'] - 4:
                at = seg['p_offset'] + offset
                return int.from_bytes(program[at:at + 4], 'little')
        return 0
    for section in elf.iter_sections():
        if section['sh_type'] in ('SHT_DYNSYM', 'SHT_SYMTAB'):
            for symbol in section.iter_symbols():
                name, value = symbol.name, symbol['st_value']
                if name:
                    result.types[name] = symbol['st_info']['type']
                if symbol['st_shndx'] == 'SHN_UNDEF':
                    if name:
                        result.imports.add(name)
                elif name:
                    result.values[name] = value
                    if symbol['st_info']['type'] == 'STT_FUNC':
                        result.functions.add(value & ~1)
                        if name.endswith(('_from_thumb', '_from_arm', '_veneer')) or 'LongThunk_' in name:
                            result.veneers.add(value & ~1)
                    if symbol['st_info']['type'] == 'STT_FUNC' and value & 1:
                        result.thumb.add(value & ~1)
        if section['sh_type'] == 'SHT_DYNAMIC':
            for tag in section.iter_tags():
                if tag.entry.d_tag == 'DT_PLTGOT':
                    result.got_base = tag.entry.d_ptr
    result.bss = [(s['sh_addr'], s['sh_addr'] + s['sh_size']) for s in elf.iter_sections()
                  if s['sh_type'] == 'SHT_NOBITS' and s['sh_flags'] & 2]
    got = elf.get_section_by_name('.got')
    # R_ARM_GOT_BREL is based at _GLOBAL_OFFSET_TABLE_, which LLD can place
    # in .got while DT_PLTGOT points at a separate .got.plt.
    if '_GLOBAL_OFFSET_TABLE_' in result.values:
        result.got_base = result.values['_GLOBAL_OFFSET_TABLE_']
    elif got is not None:
        result.got_base = got['sh_addr']
    got_ranges = [(s['sh_addr'], s['sh_addr'] + s['sh_size']) for s in elf.iter_sections()
                  if s.name in ('.got', '.got.plt')]
    for section in elf.iter_sections():
        if not isinstance(section, RelocationSection):
            continue
        symtab = elf.get_section(section['sh_link'])
        for rel in section.iter_relocations():
            at, kind = rel['r_offset'], rel['r_info_type']
            symbol = symtab.get_symbol(rel['r_info_sym'])
            if kind in (2, 21, 22) and symbol['st_shndx'] == 'SHN_UNDEF' and symbol.name:
                result.slots[at] = (symbol.name, None)
            if not any(lo <= at < hi for lo, hi in got_ranges):
                continue
            if kind in (2, 21, 22):  # ABS32, GLOB_DAT, JUMP_SLOT
                value = None if symbol['st_shndx'] == 'SHN_UNDEF' else symbol['st_value']
                if value is not None and kind != 22:
                    value = (value + word(at)) & 0xFFFFFFFF
                result.slots[at] = (symbol.name, value)
            elif kind == 23:  # RELATIVE: file word is the local pointer before the loader adds the load bias
                result.slots[at] = ('', word(at))
    plt = elf.get_section_by_name('.plt')
    if plt is not None:
        reader = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
        reader.detail = True
        base, entry = None, None
        for ins in reader.disasm(plt.data(), plt['sh_addr']):
            ops = ins.operands
            if ins.mnemonic == 'add' and len(ops) >= 3 and ops[2].type == capstone.arm.ARM_OP_IMM:
                dst, src = ins.reg_name(ops[0].reg), ins.reg_name(ops[1].reg)
                encoded = int.from_bytes(ins.bytes, 'little')
                imm8, rotate = encoded & 255, ((encoded >> 8) & 15) * 2
                immediate = ((imm8 >> rotate) | (imm8 << (32 - rotate))) & 0xFFFFFFFF if rotate else imm8
                if dst in ('ip', 'r12') and src == 'pc':
                    base, entry = ins.address + 8 + immediate, ins.address
                elif dst in ('ip', 'r12') and src in ('ip', 'r12') and base is not None:
                    base += immediate
                else:
                    base = None
            elif ins.mnemonic == 'ldr' and len(ops) == 2 and ins.reg_name(ops[0].reg) == 'pc' and base is not None:
                if ops[1].type == capstone.arm.ARM_OP_MEM and ins.reg_name(ops[1].mem.base) in ('ip', 'r12'):
                    item = result.slots.get((base + ops[1].mem.disp) & 0xFFFFFFFF)
                    if item is not None and item[0]:
                        result.plt[item[0]].append(entry)
                base = None
            else:
                base = None
    for name, value in result.values.items():
        result.plt_values[value] += result.plt.get(name, [])
    return result
