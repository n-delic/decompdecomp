"""Matching C against code taken straight from a ROM.

The target is final, linked machine code: every address is already filled in. A candidate is a
freshly compiled object whose references are relocations against names. To compare them fairly we
*link* the candidate at the target's address:

- names (func_80001234, D_80012340, or anything renamed in the symbol table) resolve to addresses;
- jumps inside the function resolve against the function's own address;
- data the candidate defines itself (string literals, float constants, jump tables, statics) is
  placed where the ROM keeps the data this instruction refers to, and its contents are checked
  against the ROM bytes there (relocated words, like jump table entries, included).

A match means the linked candidate is byte-for-byte the code in the ROM. For the diff the agent
reads, both sides are shown with the same names (the symbol table's names for those addresses).
"""

from __future__ import annotations

import re
import struct
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from typing import Callable

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from .. import scoring
from ..candidate_work import elf as candidate_elf
from ..elfobj import (
    MASKS_BY_ARCH,
    FunctionObj,
    MipsRelocationPairs,
    Reloc,
    canonical_symbol,
    section_target,
    symbol_at,
    unmangle,
)
from .link import DataMismatch, LinkResult, significant  # (re-exported: callers still ask match.LinkResult)
from .split import RomFunction
from .symbols import AUTO_NAME, SymbolTable

R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16 = 2, 4, 5, 6, 7
R_MIPS_LITERAL = 8
_REF = re.compile(r"%(hi|lo|gp_rel)\(\s*([A-Za-z_.$][\w.$]*)\s*(?:([+-])\s*(0x[0-9A-Fa-f]+|\d+))?\s*\)")
_MASKED_LOW = re.compile(r"%(lo)\(\s*([A-Za-z_.$][\w.$]*)\s*([+-])\s*\(\s*"
                         r"(0x[0-9A-Fa-f]+|\d+)\s*&\s*(?:0x[fF]{4}|65535)\s*\)\s*\)")
_KIND = {"hi": 5, "lo": 6, "gp_rel": 7}
_JUMPS = {"jal", "j"}
# Bytes a load or store moves, by opcode (lwl/lwr/swl/swr read parts of words, so they don't say)
_WIDTH = {0x20: 1, 0x24: 1, 0x28: 1, 0x21: 2, 0x25: 2, 0x29: 2, 0x23: 4, 0x27: 4, 0x2B: 4, 0x31: 4, 0x39: 4, 0x32: 4,
          0x3A: 4, 0x35: 8, 0x3D: 8, 0x37: 8, 0x3F: 8, 0x1E: 16, 0x1F: 16, 0x36: 16, 0x3E: 16}

ReadFn = Callable[[int, int], "bytes | None"]
# Only the hosted worker's per-request protocol can install this routine; ordinary attempts stay in-process.
instruction_linker: ContextVar[Callable[..., tuple[bytes, bool]] | None] = ContextVar("instruction_linker", default=None)
elf_preparer: ContextVar[Callable[[bytes, str], dict | None] | None] = ContextVar("elf_preparer", default=None)


@dataclass
class RomTarget:
    name: str
    vram: int
    code: bytes
    relocs: list[tuple[int, int, int]]  # (offset in function, reloc type, absolute address)
    big_endian: bool = True
    gp: int = 0  # $gp, for $gp-relative references (PlayStation small data)

    @property
    def order(self) -> str:
        return "big" if self.big_endian else "little"

    def to_json(self) -> list:
        return [list(r) for r in self.relocs]

    @staticmethod
    def relocs_from_json(v: list) -> list[tuple[int, int, int]]:
        return [tuple(r) for r in v]


def encode(rtype: int, residual: int) -> int:
    """The relocation field an assembler writes for `symbol + residual` (REL, MIPS)."""
    if rtype == R_MIPS_26:
        return (residual >> 2) & 0x03FFFFFF
    if rtype == R_MIPS_HI16:
        return ((residual + 0x8000) >> 16) & 0xFFFF
    if rtype == R_MIPS_LO16:
        return residual & 0xFFFF
    return residual & 0xFFFFFFFF


def _patch(word: int, rtype: int, address: int, gp: int = 0) -> int:
    if rtype == R_MIPS_GPREL16:
        return (word & ~0xFFFF) | ((address - gp) & 0xFFFF)
    if rtype == R_MIPS_26:
        return (word & ~0x03FFFFFF) | ((address >> 2) & 0x03FFFFFF)
    if rtype == R_MIPS_HI16:
        return (word & ~0xFFFF) | (((address + 0x8000) >> 16) & 0xFFFF)
    if rtype == R_MIPS_LO16:
        return (word & ~0xFFFF) | (address & 0xFFFF)
    if rtype == R_MIPS_32:
        return address & 0xFFFFFFFF
    raise ValueError(rtype)


# ----------------------------------------------------------------------------------- target

def target_from_split(fn: RomFunction, resolve: Callable[[str], int | None], big_endian: bool = True,
                      gp: int = 0) -> RomTarget:
    """The function's bytes from the ROM plus the address behind every annotated reference."""
    code = b"".join(ln.word.to_bytes(4, "big" if big_endian else "little") for ln in fn.lines)
    relocs: list[tuple[int, int, int]] = []
    for index, ln in enumerate(fn.lines):
        off = ln.vram - fn.vram
        m = _REF.search(ln.operands)
        masked = _MASKED_LOW.search(ln.operands) if m is None else None
        m = m or masked
        if m:
            kind, sym, sign, num = m.groups()
            base = resolve(sym)
            if base is not None:
                addend = (int(num, 0) if num else 0) * (-1 if sign == "-" else 1)
                address = (base + addend) & 0xffffffff
                if masked:
                    # A masked low addend alone names several possible full addresses. The
                    # preceding annotated high and actual instruction pair must select this one.
                    if not index or (off - 4, R_MIPS_HI16, address) not in relocs:
                        continue
                    previous = fn.lines[index - 1]
                    if previous.vram + 4 != ln.vram or _adjacent_low_address(previous.word, ln.word) != address:
                        continue
                relocs.append((off, _KIND[kind], address))
            continue
        if ln.mnemonic in _JUMPS:
            op = ln.operands.strip()
            if label := re.fullmatch(r"\.?L([0-9A-Fa-f]{8})", op):  # not a function named L... (`j LoadBTF`)
                addr = int(label[1], 16)
            else:
                addr = resolve(op)
                if addr is None:
                    addr = ((ln.vram + 4) & 0xF0000000) | ((ln.word & 0x03FFFFFF) << 2)
            relocs.append((off, R_MIPS_26, addr))
    return RomTarget(fn.name, fn.vram, code, relocs, big_endian, gp)


def _adjacent_low_address(high: int, low: int) -> int | None:
    register = (high >> 16) & 31
    opcode = low >> 26
    if high >> 26 != 15 or not register or (low >> 21) & 31 != register:
        return None
    if opcode != 9 and opcode not in _WIDTH:
        return None
    immediate = low & 0xffff
    signed = immediate - 0x10000 if immediate & 0x8000 else immediate
    return (((high & 0xffff) << 16) + signed) & 0xffffffff


def _display_relocs(t: RomTarget):
    """Complete an older split's omitted low annotation without changing the linked target.

    Masked constant addends were once missed by the assembly reader. Only infer the adjacent
    low instruction when its actual register flow and raw bytes prove the annotated address.
    """
    result = list(t.relocs)
    occupied = {off for off, _, _ in result}
    for off, kind, addr in t.relocs:
        if kind != R_MIPS_HI16 or off < 0 or off % 4 or off + 8 > len(t.code) or off + 4 in occupied:
            continue
        high = int.from_bytes(t.code[off:off+4], t.order)
        low = int.from_bytes(t.code[off+4:off+8], t.order)
        if _adjacent_low_address(high, low) == addr:
            result.append((off + 4, R_MIPS_LO16, addr))
    return sorted(result)


def canonical_target(t: RomTarget, symbols: SymbolTable) -> FunctionObj:
    relocs = []
    for off, rtype, addr in _display_relocs(t):
        name, residual = symbols.name_at(addr)
        if t.vram <= addr < t.vram + len(t.code):
            name, residual = t.name, addr - t.vram
        relocs.append(Reloc(off, rtype, name, encode(rtype, residual)))
    return FunctionObj(t.name, "mips", t.big_endian, t.code, relocs)


# -------------------------------------------------------------------------------- candidate

def raw_relocs(elf: ELFFile, shndx: int, start: int, size: int, code: bytes) -> list[dict]:
    order = "little" if elf.little_endian else "big"
    raw = []
    for rsec in elf.iter_sections():
        if not isinstance(rsec, RelocationSection) or rsec["sh_info"] != shndx:
            continue
        symtab = elf.get_section(rsec["sh_link"])
        for rel in rsec.iter_relocations():
            off = rel["r_offset"]
            if not (start <= off < start + size):
                continue
            rtype = rel["r_info_type"]
            if rel.is_RELA():
                value = rel["r_addend"]
            else:
                delta, length, mask = MASKS_BY_ARCH.get(("mips", rtype), (0, 4, 0xFFFFFFFF))
                at = off - start + delta
                value = int.from_bytes(code[at:at + length], order) & mask
            raw.append({"off": off - start, "type": rtype, "symidx": rel["r_info_sym"], "rela": rel.is_RELA(),
                        "value": value, "symtab": symtab, "order": len(raw)})
    raw.sort(key=lambda r: r["off"])
    return raw


# Names a compiler writes into code the programmer never named: the C library's math and string routines it calls
# for a builtin, libgcc's helpers (__divdi3), and the assembler-level helpers older compilers reference (MULT_HI).
_LIBRARY_CALLS = frozenset("sqrt sqrtf sin sinf cos cosf tan tanf fabs fabsf memcpy memmove memset memcmp strlen "
                           "bcopy bzero bcmp".split())


def compiler_library_name(name: str) -> bool:
    if AUTO_NAME.match(name):  # D_80012340 names its own address
        return False
    return name in _LIBRARY_CALLS or name.startswith("__") or bool(re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", name))


class _Linker:
    def __init__(self, obj: bytes, name: str, target: RomTarget, symbols: SymbolTable, read: ReadFn, *,
                 prefer_base: bool = False, prepared: dict | None = None):
        self.prefer_base = prefer_base  # place own unnamed data by the voted base even where the target refers elsewhere
        self.obj, self.prepared = obj, prepared
        self.name, self.target, self.symbols, self.read = name, target, symbols, read
        self.problems: list[str] = []
        self.library_names: dict[str, int] = {}  # compiler-emitted library call -> the address the game's call has
        self.data_mismatches: list[DataMismatch] = []
        self.missing_data_reads: list[tuple[int, int]] = []
        self.required_data_reads: list[tuple[int, int, bytes | None]] = []
        self.local_map: dict[tuple[int, int], int] = {}  # (section index, offset) -> ROM address
        self.target_at = {off: (rtype, addr) for off, rtype, addr in target.relocs}
        self.data: dict[tuple[int, int], int] = {}  # every datum of its own the candidate uses -> ROM address
        self.widths: dict[tuple[int, int], int | None] = {}  # bytes the code loads from it (None: its address is used)
        self._data_relocs: dict[int, list] = {}
        self._holds: dict[tuple[int, int, int], bool] = {}  # holds_own_word's answers
        self.words: list[int] = []
        self.fn_shndx = None

    @cached_property
    def elf(self):
        return candidate_elf(self.obj, ELFFile)

    @cached_property
    def symtab(self):
        return self.elf.get_section_by_name(".symtab")

    @cached_property
    def gp0(self):
        # the $gp the assembler assumed (.reginfo's ri_gp_value): a $gp-relative reference to the object's own data
        # holds its offset less this (EE-GCC: -0x4000 for the first small datum)
        reginfo = self.elf.get_section_by_name(".reginfo")
        raw = reginfo.data() if reginfo is not None else b""
        return struct.unpack_from("<i" if self.elf.little_endian else ">i", raw, 20)[0] if len(raw) >= 24 else 0

    def problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def function(self) -> tuple[int, int, int] | None:
        """(section index, offset, size) of the submitted function, also for period assemblers' size-0 symbols."""
        from ..elfobj import _function_symbols

        return _function_symbols(self.elf).get(self.name)

    def resolve_name(self, name: str) -> int | None:
        addr = self.symbols.resolve(name)
        if addr is None:
            self.problem(f"`{name}` is not a known symbol. Use the name the assembly shows (like D_80012340 or "
                         f"func_80001234), or give that address this name first with rename_symbol.")
        return addr

    def library_call(self, name: str, r: dict, residual: int) -> int | None:
        """The address a compiler-emitted library call (`sqrtf`, a libgcc helper) names, from the game's own call.
        The game's code reaches that routine under its automatic name (func_80055B40), so `sqrtf` is no symbol of
        the table and a rename to it is refused (a builtin's spelling changes how the compiler calls it). The
        target has a `jal` at this very place: its address is what the name means here, and the judge still compares
        the linked bytes. A name already in the table, or one two calls place differently, is left to resolve_name."""
        if r["type"] != R_MIPS_26 or self.symbols.resolve(name) is not None or not compiler_library_name(name):
            return None
        want = self.target_at.get(r["off"])
        if want is None or want[0] != R_MIPS_26:
            return None
        base = (want[1] - residual) & 0xFFFFFFFF
        if self.library_names.setdefault(name, base) != base:
            self.problem(f"`{name}` is called at two different ROM addresses (0x{self.library_names[name]:08X} and "
                         f"0x{base:08X}); the game's code reaches them by their own names")
            return None
        return base

    def address(self, raw: list[dict], k: int, fn_shndx: int, fn_start: int, *,
                pairs: MipsRelocationPairs | None = None) -> int | None:
        r = raw[k]
        if r['type'] == R_MIPS_LITERAL:
            if not self.target.gp:
                self.problem('R_MIPS_LITERAL (8) needs the target $gp, which is not configured. Check actual target '
                             '$gp use and compiler small-data settings (-G0 avoids generated $gp-relative literals); '
                             'do not guess $gp or literal placement.')
            else:
                self.problem('R_MIPS_LITERAL (8) literal-pool linking is not supported here yet, even with a known '
                             'target $gp. Do not guess literal placement.')
            return None
        if self.prepared is not None:
            residual = r["residual"]
        elif r["type"] == R_MIPS_GPREL16:
            residual = r["value"] - 0x10000 if r["value"] & 0x8000 else r["value"]
        else:
            residual = section_target("mips", raw, k, pairs=pairs)
        if residual is None:
            self.problem(f"relocation type {r['type']} is not supported here")
            return None
        if r["type"] == R_MIPS_GPREL16 and not self.target.gp:
            self.problem("your code uses $gp-relative data, but this game doesn't: compile with -G0")
            return None
        if self.prepared is None:
            sym = r["symtab"].get_symbol(r["symidx"])
            shndx = sym["st_shndx"]
            local = sym["st_info"]["bind"] == "STB_LOCAL"
            section_symbol = sym["st_info"]["type"] == "STT_SECTION"
            if r["type"] == R_MIPS_GPREL16 and local:
                residual += self.gp0
            external = canonical_symbol(sym.name, sym["st_info"]["bind"]) if shndx in (
                "SHN_UNDEF", "SHN_COMMON", 0xFF03, 0xFF04) else None
            off = residual if section_symbol else sym["st_value"] + residual
            named = symbol_at(self.symtab, shndx, off) if external is None else None
        else:
            shndx, local, section_symbol = r["section"], r["local"], r["section_symbol"]
            external, off, named = r["external"], r["section_off"], self.prepared_name(r)
        if external is not None:
            if (base := self.library_call(external, r, residual)) is None:
                base = self.resolve_name(external)
            return None if base is None else (base + residual) & 0xFFFFFFFF
        if shndx == fn_shndx:
            if named and named[0] == self.name:
                return self.target.vram + named[1]
            if named is None:
                self.problem(f"a jump into .text+0x{off:X} that isn't part of {self.name}")
                return None
            base = self.resolve_name(named[0])
            return None if base is None else base + named[1]
        # data this submission defines itself: place it where the ROM keeps the data this instruction uses
        base = self.symbols.resolve(named[0]) if named else None
        if base is not None:
            addr = base + named[1]
            if section_symbol or local:
                self.local_map.setdefault((shndx, off), addr)  # g++'s `.sdata+4`: the export places the section
        else:
            want = self.target_at.get(r["off"])
            if (self.prefer_base or want is None or want[0] != r["type"]) and (
                    placed := self.section_place(shndx, off, r["type"])):
                want = (r["type"], placed)
            if want is None or want[0] != r["type"]:
                label = f"{named[0]}+0x{named[1]:X}" if named else f"{self.elf.get_section(shndx).name}+0x{off:X}"
                self.problem(f"your code refers to {label} where the ROM's code has no address")
                return None
            addr = want[1]
            prev = self.local_map.setdefault((shndx, off), addr)
            if prev != addr:
                self.problem(f"{self.elf.get_section(shndx).name}+0x{off:X} is used for two different ROM addresses")
                return None
        self.data.setdefault((shndx, off), addr)
        if r["type"] != R_MIPS_HI16:  # the instruction that uses the address says how much of the data it reads
            width = _WIDTH.get(self.words[r["off"] // 4] >> 26) if r["off"] // 4 < len(self.words) else None
            prev_width = self.widths.get((shndx, off), 0)
            self.widths[(shndx, off)] = None if width is None or prev_width is None else max(prev_width, width)
        return addr

    def prepared_name(self, row):
        if (row["named"] is None and row["section_off"] is not None and self.prepared["mdebug"]):
            # IDO can leave private procedures only in ECOFF metadata.
            return symbol_at(self.symtab, row["section"], row["section_off"])
        return row["named"]

    # ------------------------------------------------------------------ the candidate's own data

    # How far (in bytes) from a reference of ours the target's references may be to vote for where our section sits.
    BASE_WINDOW = 0x200

    def section_bases(self, raw: list[dict], fn_shndx: int, pairs: MipsRelocationPairs | None) -> None:
        """Where each of the candidate's own unnamed data sections (an assembler's float literal pool, a jump table)
        sits in the ROM, voted by the target's references near each of ours: (ROM address - offset) counted over
        every pair of a reference of ours and one of the target's of the same type within BASE_WINDOW.

        A literal pool is laid out in the order the code uses it, so one base places all of it; the target's
        reference at the very same instruction (what `address` asks first) is only there while the code above is
        aligned. A 1,389-instruction state machine whose source used the game's literals (`li.s`, 40 of them) scored
        26102 with each misplaced one charged as an unknown address, against 19121 for the same code with the pool
        declared as `extern f32 D_...`, a form that can never match (the target loads them through `$at`). Only a
        base that wins at least two votes counts, and `section_place` only places an offset the target really
        refers to; `verify_data` still compares the data with the ROM, so a match is decided as before."""
        self.bases: dict[int, int] = {}
        votes: dict[int, dict[int, int]] = {}
        by_type: dict[int, list[tuple[int, int]]] = {}
        for off, (rtype, addr) in self.target_at.items():
            by_type.setdefault(rtype, []).append((off, addr))
        for k, r in enumerate(raw):
            if r["type"] not in (R_MIPS_HI16, R_MIPS_LO16):
                continue
            if self.prepared is None:
                sym = r["symtab"].get_symbol(r["symidx"])
                shndx = sym["st_shndx"]
                if not isinstance(shndx, int) or shndx == fn_shndx or shndx >= 0xFF00:
                    continue
                residual = section_target("mips", raw, k, pairs=pairs)
                if residual is None:
                    continue
                off = residual if sym["st_info"]["type"] == "STT_SECTION" else sym["st_value"] + residual
                named = symbol_at(self.symtab, shndx, off)
            else:
                shndx, off = r["section"], r["section_off"]
                if off is None or shndx == fn_shndx or shndx >= 0xFF00:
                    continue
                named = self.prepared_name(r)
            if named and self.symbols.resolve(named[0]) is not None:
                continue
            tally = votes.setdefault(shndx, {})
            for toff, taddr in by_type.get(r["type"], ()):
                if abs(toff - r["off"]) <= self.BASE_WINDOW:
                    base = (taddr - off) & 0xFFFFFFFF
                    if self.holds_own_word(shndx, off, base):
                        tally[base] = tally.get(base, 0) + 1
        for shndx, tally in votes.items():
            ranked = sorted(tally.items(), key=lambda kv: -kv[1])
            if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
                self.bases[shndx] = ranked[0][0]

    def holds_own_word(self, shndx: int, off: int, base: int) -> bool:
        """Can the ROM keep the candidate's datum at `off` at `base + off`: the ROM's bytes there are the datum's
        (its first word, or less when the section ends sooner). A word with a relocation in it and a section with
        no bytes (.bss) can: their bytes are only known after linking. A jump table's entry is the exception: it
        is relocated against this function's own code, so the ROM's word there must be an address inside the
        target function. A switch whose table was voted onto a record array the function indexes more often than
        its table showed two wrong `%hi`/`%lo` rows (300 points) that the source did not have.

        A 72-instruction SN function with one `li.s` literal had it voted to a global pointer's address, because
        that global's %lo appears three times within BASE_WINDOW and the pool's own address only once per half
        (score 70 before the vote, 365 after, for the same source). The pointer's address is .bss: no ROM bytes,
        so no literal can sit there."""
        cache = self._holds
        key = (shndx, off, base)
        if key not in cache:
            sec = self.elf.get_section(shndx)
            mine = b"" if sec["sh_type"] == "SHT_NOBITS" else sec.data()[off:off + 4]
            relocated = any(off - 4 < at < off + 4 for at, _, _ in self._relocs_in(shndx))
            entry = any(at == off and rtype == R_MIPS_32 and symbol["st_shndx"] == self.fn_shndx
                        for at, rtype, symbol in self._relocs_in(shndx))
            if entry:
                word = self.read((base + off) & 0xFFFFFFFF, 4)
                at = int.from_bytes(word, self.target.order) | 0x80000000 if word and len(word) == 4 else 0
                start = self.target.vram | 0x80000000  # some games store the words without the KSEG0 bit
                cache[key] = start <= at < start + len(self.target.code)
            elif not mine or relocated:
                cache[key] = True
            else:
                cache[key] = self.read((base + off) & 0xFFFFFFFF, len(mine)) == mine
        return cache[key]

    def section_place(self, shndx: int, off: int, rtype: int) -> int | None:
        """The ROM address of `off` in one of the candidate's own sections by its voted base, when the target
        refers to that address with a reference of the same type somewhere in the function."""
        base = getattr(self, "bases", {}).get(shndx)
        if base is None:
            return None
        addr = (base + off) & 0xFFFFFFFF
        return addr if any(t == rtype and a == addr for t, a in self.target_at.values()) else None

    def _relocs_in(self, shndx: int):
        """(offset, type, symbol) of relocations inside one of the candidate's data sections."""
        if shndx not in self._data_relocs:
            out = []
            for rsec in self.elf.iter_sections():
                if isinstance(rsec, RelocationSection) and rsec["sh_info"] == shndx:
                    rsym = self.elf.get_section(rsec["sh_link"])
                    out += [(rel["r_offset"], rel["r_info_type"], rsym.get_symbol(rel["r_info_sym"]))
                            for rel in rsec.iter_relocations()]
            self._data_relocs[shndx] = sorted(out, key=lambda x: x[0])
        return self._data_relocs[shndx]

    def extent(self, shndx: int, off: int) -> int:
        """Length of the datum at `off`: up to the next symbol or the next place this candidate uses (period
        assemblers don't record sizes; string literals have no symbol at all)."""
        sec = self.elf.get_section(shndx)
        size = sec["sh_size"]
        sym = next((s for s in self.symtab.iter_symbols() if s["st_shndx"] == shndx and s["st_value"] == off
                    and s["st_size"] and s["st_info"]["type"] == "STT_OBJECT"), None)
        if sym is not None:
            return max(0, min(sym["st_size"], size - off))
        bounds = {s["st_value"] for s in self.symtab.iter_symbols() if s["st_shndx"] == shndx
                  and s["st_info"]["type"] not in ("STT_SECTION", "STT_FILE")} | {o for (i, o) in self.data if i == shndx}
        return next((b for b in sorted(bounds) if b > off), size) - off

    def _pointee(self, s, value: int) -> tuple[int | None, tuple[int, int] | None]:
        """(address, own datum) a word of the candidate's data points at: (address, None) for a named thing,
        (None, (section, offset)) for more of the candidate's own data, (None, None) when it can't be placed."""
        if s["st_shndx"] in ("SHN_UNDEF", "SHN_COMMON") or s["st_shndx"] in (0xFF03, 0xFF04):
            base = self.resolve_name(canonical_symbol(s.name, s["st_info"]["bind"]))
            return (None if base is None else base + value), None
        in_sec = value if s["st_info"]["type"] == "STT_SECTION" else s["st_value"] + value
        hit = symbol_at(self.symtab, s["st_shndx"], in_sec)
        if hit and hit[0] == self.name:
            return self.target.vram + hit[1], None
        base = self.symbols.resolve(hit[0]) if hit else None
        if base is not None:
            return base + hit[1], None
        if s["st_shndx"] == self.fn_shndx:
            return None, None
        return None, (s["st_shndx"], in_sec)

    def place_pointees(self) -> None:
        """Data the candidate's data points at (a table of strings) goes where the ROM's pointers point."""
        todo = list(self.data)
        while todo:
            shndx, off = todo.pop()
            if self.elf.get_section(shndx)["sh_type"] == "SHT_NOBITS":
                continue
            addr, length = self.data[(shndx, off)], self.extent(shndx, off)
            data = self.elf.get_section(shndx).data()
            for at, rtype, s in self._relocs_in(shndx):
                if rtype != R_MIPS_32 or not off <= at <= off + length - 4:
                    continue
                _, own = self._pointee(s, int.from_bytes(data[at:at + 4], self.target.order))
                if own is None or own in self.data:
                    continue
                pointer = addr + at - off
                rom = self.required_read(pointer, 4)
                if rom is None:
                    continue
                self.local_map[own] = self.data[own] = int.from_bytes(rom, self.target.order)
                self.widths[own] = None
                todo.append(own)

    def significant(self, mine: bytes, addr: int, width: int | None) -> int:
        starts = [*self.data.values(), *(a for _, a in self.target_at.values())]
        return significant(mine, addr, width, lambda lo: min(
            [a for a in (self.symbols.next_after(lo - 1), *starts) if a is not None and a >= lo], default=None))

    def switch_extent(self, address, offsets):
        if not 2 <= len(offsets) <= 256 or offsets != set(range(0,len(offsets)*4,4)):
            return None
        from ..arch.mips_indirect import jump_table

        code = self.target.code
        order = self.target.order
        for at in range(24,len(code)-7,4):
            instruction = int.from_bytes(code[at:at+4],order)
            if instruction & 0xfc1fffff != 8 or instruction >> 21 & 31 == 31:
                continue
            witness = jump_table(code,0,len(code),self.target.vram,at,order,reader=self.required_read)
            if (witness and witness['state']=='validated' and witness['table_address']==address
                    and witness['entry_count']==len(offsets)):
                return witness['entry_count']*4
        return None

    def required_read(self, addr: int, size: int) -> bytes | None:
        data = self.read(addr, size)
        self.required_data_reads.append((addr, size, data))
        if data is None:
            self.missing_data_reads.append((addr, size))
        return data

    def verify_data(self, shndx: int, off: int, addr: int, *, instructions_exact: bool = False) -> None:
        """Data the candidate defines must be identical to the ROM data it stands for."""
        sec = self.elf.get_section(shndx)
        if sec["sh_type"] == "SHT_NOBITS":
            return
        length = self.extent(shndx, off)
        if length <= 0:
            return
        mine = bytearray(sec.data()[off:off + length])
        code_pointers = set()
        for at, rtype, s in self._relocs_in(shndx):  # relocated words inside the data, e.g. jump tables
            at -= off
            if rtype != R_MIPS_32 or not 0 <= at <= length - 4:
                continue
            dest, own = self._pointee(s, int.from_bytes(mine[at:at + 4], self.target.order))
            if own is not None:
                dest = self.local_map.get(own)
            if dest is None:
                self.problem(f"can't place a pointer inside your data at {sec.name}+0x{off + at:X}")
                return
            mine[at:at + 4] = (dest & 0xFFFFFFFF).to_bytes(4, self.target.order)
            if self.target.vram <= dest < self.target.vram + len(self.target.code):
                code_pointers.add(at)
        n = self.significant(bytes(mine), addr, self.widths.get((shndx, off)))
        if instructions_exact and n < 4*len(code_pointers):
            # Indexed LW resembles a scalar word load in the relocation
            # stream. Prove the dispatch extent before checking the rest.
            if extent := self.switch_extent(addr, code_pointers):
                n = extent
        rom = self.required_read(addr, n)
        if rom is None:
            # Normal judging allows a datum in BSS. A standalone export with an
            # incomplete image map must instead prove that omission is safe.
            return
        if bytes(mine[:n]) != rom:
            # The switch diagnostic needs the actual relocated words, not an
            # interpretation of the abbreviated comparison message.
            if code_pointers and len(self.data_mismatches) < 8:
                # An indexed LW can look like a single scalar read to the
                # linker. Read the relocated pointer extent for the report;
                # the switch diagnosis independently verifies its bounds.
                extent = max(code_pointers)+4
                table = self.read(addr, extent) if extent <= 1024 and extent <= length else None
                if table is not None and len(table) == extent:
                    self.data_mismatches.append(DataMismatch(addr, table, bytes(mine[:extent]), frozenset(code_pointers)))
            where = self.symbols.label(addr)
            first=next(i for i,(a,b) in enumerate(zip(mine[:n],rom)) if a!=b)
            last=max(i for i,(a,b) in enumerate(zip(mine[:n],rom)) if a!=b)
            self.problem(f"your data for {where} differs from the ROM: yours {bytes(mine[:min(n, 24)]).hex(' ')}"
                         f"{'…' if n > 24 else ''}, ROM {rom[:24].hex(' ')}{'…' if n > 24 else ''}; differing range 0x{addr+first:X}-0x{addr+last+1:X} (end exclusive)")

    def run(self) -> LinkResult:
        if self.prepared is None:
            fsym = self.function()
            if fsym is None:
                return LinkResult(None, False, [f"the object has no function named `{self.name}`"])
            shndx, start, size = fsym
            code = self.elf.get_section(shndx).data()[start:start + size]
            raw = raw_relocs(self.elf, shndx, start, size, code)
            big_endian = not self.elf.little_endian
            pairs = MipsRelocationPairs(raw)
        else:
            shndx, start, size = (self.prepared[key] for key in ("section", "start", "size"))
            code = self.obj[start:start + size]
            raw = self.prepared["relocs"]
            big_endian, pairs = self.prepared["big_endian"], None
        self.fn_shndx = shndx
        if big_endian != self.target.big_endian:
            return LinkResult(None, False, ["the compiler produced code in the wrong byte order for this game"])
        fmt = (">" if self.target.big_endian else "<") + f"{len(code) // 4}I"
        words = list(struct.unpack(fmt, code[:len(code) // 4 * 4]))
        self.words = list(words)
        relocs: list[Reloc] = []
        self.section_bases(raw, shndx, pairs)
        link_instructions = instruction_linker.get()
        patches = []
        for k, r in enumerate(raw):
            addr = self.address(raw, k, shndx, start, pairs=pairs)
            if addr is None:
                symbol = r["symtab"].get_symbol(r["symidx"]).name if self.prepared is None else r["symbol"]
                relocs.append(Reloc(r["off"], r["type"], f"?{symbol or 'local'}", 0))
                continue
            i = r["off"] // 4
            if i < len(words):
                if link_instructions is None:
                    words[i] = _patch(words[i], r["type"], addr, self.target.gp)
                else:
                    patches.append((r["off"], r["type"], addr & 0xFFFFFFFF))
            name, residual = self.symbols.name_at(addr)
            if self.target.vram <= addr < self.target.vram + len(self.target.code):
                name, residual = self.name, addr - self.target.vram
            relocs.append(Reloc(r["off"], r["type"], name, encode(r["type"], residual)))
        # every place is known before any datum is measured: a datum runs to the next one, whatever the code's order
        self.place_pointees()
        if link_instructions is None:
            linked = struct.pack(fmt[0] + f"{len(words)}I", *words)
            instructions_exact = linked == self.target.code
        else:
            linked, instructions_exact = link_instructions(code, self.target, patches)
        for (dshndx, off), addr in sorted(self.data.items()):
            self.verify_data(dshndx, off, addr, instructions_exact=instructions_exact)
        exact = not self.problems and instructions_exact
        placements: dict[str, dict[int, int]] = {}
        for (dshndx, off), addr in self.local_map.items():
            placements.setdefault(self.elf.get_section(dshndx).name, {})[off] = addr
        return LinkResult(FunctionObj(self.name, "mips", self.target.big_endian, code, relocs), exact, self.problems,
                          placements, instructions_exact, linked, self.data_mismatches, self.missing_data_reads,
                          self.required_data_reads)


def link(obj: bytes, name: str, target: RomTarget, symbols: SymbolTable, read: ReadFn) -> LinkResult:
    """Link a compiled candidate at the target's address and compare it with the ROM."""
    obj = unmangle(obj, lambda n: n in symbols.by_name or bool(AUTO_NAME.match(n)))  # C++ names: the game's
    prepare = elf_preparer.get()
    prepared = prepare(obj, name) if prepare else None
    first = _Linker(obj, name, target, symbols, read, prepared=prepared)
    result = first.run()
    if result.exact or not getattr(first, "bases", None):
        return result
    # Not a match: once the code above a literal is a word off, the target's reference at the same instruction is
    # some other datum (a global's %hi), so the first link names the wrong address. Link again placing the own
    # data by its voted base everywhere, and keep whichever has fewer problems (a pool the ROM keeps in pieces
    # gets data mismatches by the base and keeps the first). Only the diff changes: a match was decided above.
    again = _Linker(obj, name, target, symbols, read, prefer_base=True, prepared=prepared).run()
    return again if len(again.problems) <= len(result.problems) else result


def diff(target: FunctionObj, result: LinkResult) -> scoring.DiffResult:
    """Score with the usual instruction diff, but decide the match by the linked bytes."""
    d = scoring.diff(target, result.candidate)
    if result.exact:
        return scoring.DiffResult(0, d.max_score, d.rows, True)
    return scoring.DiffResult(max(1, d.score), d.max_score, d.rows, False)
