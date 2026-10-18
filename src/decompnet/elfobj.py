"""ELF object parsing: pull a single function's bytes + relocations out of a .o file.

Matching is done on *normalized* bytes: every byte range touched by a relocation is
masked out, and the relocations themselves are compared symbolically. That way a
call to `foo` matches whether `foo` lives at 0x80001234 in the ROM or is an
undefined extern in the candidate object.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from io import BytesIO

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from elftools.elf.sections import SymbolTableSection

from . import arch

# e_machine -> arch key used throughout decompnet
MACHINES = {"EM_X86_64": "x86_64", "EM_386": "x86", "EM_MIPS": "mips", "EM_PPC": "ppc", "EM_ARM": "arm"}

# (arch, reloc type) -> (byte offset from r_offset, length, big-endian bitmask to clear), from each processor's
# module in arch/. Anything not listed masks the 4 bytes at r_offset, which is right for most absolute/PC32 relocs.
MASKS_BY_ARCH: dict[tuple[str, int], tuple[int, int, int]] = arch.reloc_masks()


@dataclass(frozen=True)
class Reloc:
    offset: int  # relative to function start
    type: int
    symbol: str
    addend: int  # only meaningful (and only compared) for non-section symbols

    def key(self) -> tuple:
        return (self.offset, self.type, self.symbol, self.addend)

    def to_json(self) -> list:
        return [self.offset, self.type, self.symbol, self.addend]

    @staticmethod
    def from_json(v: list) -> Reloc:
        return Reloc(*v)


@dataclass
class FunctionObj:
    name: str
    arch: str
    big_endian: bool
    code: bytes
    relocs: list[Reloc] = field(default_factory=list)

    def normalized(self) -> bytes:
        return mask_relocs(self.code, self.relocs, self.arch, self.big_endian)

    def fingerprint(self) -> str:
        """Hash of masked bytes + symbolic relocs. Equal fingerprints == matching function."""
        h = hashlib.sha256(self.normalized())
        for r in sorted(self.relocs, key=Reloc.key):
            h.update(repr(r.key()).encode())
        return h.hexdigest()


def mask_relocs(code: bytes, relocs: list[Reloc], arch: str, big_endian: bool) -> bytes:
    """The code with every relocated field cleared, as the processor's relocation masks say: two builds that differ
    only in where things were linked compare equal."""
    buf = bytearray(code)
    for r in relocs:
        delta, length, mask = MASKS_BY_ARCH.get((arch, r.type), (0, 4, 0xFFFFFFFF))
        start = r.offset + delta
        if start < 0 or start + length > len(buf):
            continue
        order = "big" if big_endian else "little"
        val = int.from_bytes(buf[start : start + length], order)
        buf[start : start + length] = (val & ~mask & ((1 << (8 * length)) - 1)).to_bytes(length, order)
    return bytes(buf)


def _arch_of(elf: ELFFile) -> tuple[str, bool]:
    machine = elf["e_machine"]
    if machine not in MACHINES:
        raise ValueError(f"unsupported ELF machine {machine}")
    return MACHINES[machine], not elf.little_endian


_LOCAL_STATIC = re.compile(r"^(.+)\.\d+$")


def canonical_symbol(name: str, bind: str) -> str:
    """GCC names function-local statics `name.N`, where N counts statics earlier in the file, so the same
    variable gets a different N when its function is compiled on its own. Compare them by base name."""
    m = _LOCAL_STATIC.match(name) if bind == "STB_LOCAL" else None
    return m.group(1) if m else name


def _symbol_name(elf: ELFFile, symtab: SymbolTableSection, idx: int) -> tuple[str, bool]:
    """Return (name, is_section_symbol). Section symbols get the section's name, with
    `.text.foo` (from -ffunction-sections) collapsed to `foo` so static callees line up."""
    sym = symtab.get_symbol(idx)
    if sym["st_info"]["type"] == "STT_SECTION":
        sec = elf.get_section(sym["st_shndx"]).name
        if sec.startswith(".text."):
            return sec[len(".text.") :], True
        # .rodata.str1.1 / .rodata.cst4 etc. are all "rodata" as far as matching cares
        return ("." + sec.split(".")[1]) if sec.count(".") > 1 else sec, True
    return canonical_symbol(sym.name, sym["st_info"]["bind"]), False


def _sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def section_target(arch: str, raw: list[dict], k: int) -> int | None:
    """Offset inside the referenced section that a section-relative relocation points at."""
    r = raw[k]
    if r["rela"]:
        # absolute relocations only; PC-relative addends are biased by the instruction layout
        return r["value"] if (arch, r["type"]) in {("x86_64", 1), ("x86_64", 10), ("x86_64", 11), ("mips", 2),
                                                    ("mips", 4), ("mips", 5), ("mips", 6)} else None
    if arch != "mips":
        return r["value"] if r["type"] == 2 else None
    t, v = r["type"], r["value"]
    if t == 4:  # R_MIPS_26: word index
        return v << 2
    if t == 2:  # R_MIPS_32
        return v
    same = [x for x in raw if x["symidx"] == r["symidx"] and x["symtab"] is r["symtab"]]
    if t == 5:  # HI16: combine with the next LO16 of the same symbol
        lo = next((x for x in same if x["off"] > r["off"] and x["type"] == 6), None)
        return (v << 16) + _sext16(lo["value"]) if lo else v << 16
    if t == 6:  # LO16: combine with the previous HI16 of the same symbol
        hi = next((x for x in reversed(same) if x["off"] < r["off"] and x["type"] == 5), None)
        return ((hi["value"] << 16) if hi else 0) + _sext16(v)
    return None


def symbol_at(symtab: SymbolTableSection, shndx: int, offset: int) -> tuple[str, int] | None:
    """(name, offset into it) of the named symbol covering `offset` in a section, preferring exact starts,
    global symbols, and functions/objects over plain labels."""
    best = None
    named = [s for s in symtab.iter_symbols()
             if s["st_shndx"] == shndx and s.name and s.name[0] not in ".$"
             and s["st_info"]["type"] not in ("STT_SECTION", "STT_FILE")
             and not s.name.endswith(".") and not s.name.startswith("__gnu_compiled")]  # markers like gcc2_compiled.
    starts = sorted({s["st_value"] for s in named})
    for s in named:
        lo, size = s["st_value"], s["st_size"]
        if size == 0:  # period assemblers record no sizes: the symbol runs to the next one
            size = next((x for x in starts if x > lo), 1 << 32) - lo
        if not (lo == offset or lo <= offset < lo + size):
            continue
        kind = {"STT_FUNC": 2, "STT_OBJECT": 1}.get(s["st_info"]["type"], 0)
        rank = (lo == offset, kind, s["st_info"]["bind"] == "STB_GLOBAL")
        if best is None or rank > best[0]:
            best = (rank, canonical_symbol(s.name, s["st_info"]["bind"]), offset - lo)
    return (best[1], best[2]) if best else None


def _encode(arch: str, r: dict, residual: int) -> int:
    """The relocation field value an assembler writes for a reference to `symbol + residual`."""
    if r["rela"] or arch != "mips":
        return residual
    t = r["type"]
    if t == 4:
        return (residual >> 2) & 0x03FFFFFF
    if t == 5:
        return ((residual + 0x8000) >> 16) & 0xFFFF
    if t == 6:
        return residual & 0xFFFF
    return residual & 0xFFFFFFFF


_MARKERS = ("gcc2_compiled.", "__gnu_compiled_c", "__gnu_compiled_cplusplus")


def _function_symbols(elf) -> dict[str, tuple[int, int, int]]:
    """name -> (section index, offset, size) of every function defined in the object.

    Modern assemblers mark functions (STT_FUNC, with a size). Period ones, like the binutils 2.6 that PSY-Q-era
    GCC used, emit untyped, size-0 symbols: there a function is a symbol in an executable section, running to
    the next one, without trailing alignment padding (but with its last delay slot)."""
    symtab = elf.get_section_by_name(".symtab")
    if symtab is None:
        return {}
    out: dict[str, tuple[int, int, int]] = {}
    loose: dict[int, list[tuple[int, str]]] = {}
    for s in symtab.iter_symbols():
        shndx = s["st_shndx"]
        if not s.name or not isinstance(shndx, int) or s["st_info"]["type"] in ("STT_SECTION", "STT_FILE"):
            continue
        if s["st_info"]["type"] == "STT_FUNC" and s["st_size"] > 0:
            out[s.name] = (shndx, s["st_value"], s["st_size"])
        elif s["st_size"] == 0 and s.name not in _MARKERS and s.name[0] not in ".$" \
                and elf.get_section(shndx)["sh_flags"] & 0x4:  # SHF_EXECINSTR
            loose.setdefault(shndx, []).append((s["st_value"], s.name))
    for shndx, syms in loose.items():
        section = elf.get_section(shndx)
        data = section.data()
        starts = sorted({off for off, _ in syms} | {off for n, (sx, off, _) in out.items() if sx == shndx})
        little = elf.little_endian
        for off, name in syms:
            if name in out:
                continue
            nxt = next((x for x in starts if x > off), section["sh_size"])
            end = nxt
            while end - 4 >= off + 4 and data[end - 4:end] == b"\0\0\0\0":
                end -= 4
            if end < nxt and end - 4 >= off:  # keep the delay slot of a final jr/branch
                word = int.from_bytes(data[end - 4:end], "little" if little else "big")
                if word == 0x03E00008 or (word >> 26) in (1, 2, 3, 4, 5, 6, 7) or (word & 0xFC00003F) == 0x00000008:
                    end += 4
            if end > off:
                out[name] = (shndx, off, end - off)
    return out


def list_functions(data: bytes) -> list[str]:
    return list(_function_symbols(ELFFile(BytesIO(data))))


def extract_function(data: bytes, name: str) -> FunctionObj | None:
    """One function of an object file: its code, its relocations (with what each refers to) and the processor; None
    when the object has no function of that name."""
    elf = ELFFile(BytesIO(data))
    arch, big_endian = _arch_of(elf)
    found = _function_symbols(elf).get(name)
    if found is None:
        return None
    symtab = elf.get_section_by_name(".symtab")
    shndx, start, size = found
    section = elf.get_section(shndx)
    code = section.data()[start : start + size]

    raw: list[dict] = []
    for rsec in elf.iter_sections():
        if not isinstance(rsec, RelocationSection) or rsec["sh_info"] != shndx:
            continue
        rsymtab = elf.get_section(rsec["sh_link"])
        for rel in rsec.iter_relocations():
            off = rel["r_offset"]
            if not (start <= off < start + size):
                continue
            rtype = rel["r_info_type"]
            if rel.is_RELA():
                value = rel["r_addend"]
            else:
                # REL (MIPS): the addend lives in the masked instruction field itself
                delta, length, mask = MASKS_BY_ARCH.get((arch, rtype), (0, 4, 0xFFFFFFFF))
                field_at = off - start + delta
                value = int.from_bytes(code[field_at : field_at + length], "big" if big_endian else "little") & mask
            raw.append({"off": off - start, "type": rtype, "symidx": rel["r_info_sym"], "rela": rel.is_RELA(),
                        "value": value, "symtab": rsymtab})
    raw.sort(key=lambda r: r["off"])

    relocs: list[Reloc] = []
    for k, r in enumerate(raw):
        sym_name, is_section = _symbol_name(elf, r["symtab"], r["symidx"])
        addend = r["value"]
        if is_section:
            # The compiler referenced a same-file function or static by section+offset. Resolve it to the
            # named symbol at that offset, encoded the way a reference to that symbol by name would be,
            # so a function compiled on its own (which references neighbours by name) can still match.
            addend = 0
            sec_idx = r["symtab"].get_symbol(r["symidx"])["st_shndx"]
            target = section_target(arch, raw, k)
            named = symbol_at(symtab, sec_idx, target) if target is not None else None
            if named:
                sym_name, residual = named
                addend = _encode(arch, r, residual)
        relocs.append(Reloc(r["off"], r["type"], sym_name, addend))
    relocs.sort(key=lambda r: r.offset)
    return FunctionObj(name=name, arch=arch, big_endian=big_endian, code=code, relocs=relocs)


_GCC2_MANGLED = re.compile(r"([A-Za-z_]\w*?)__(?:[FQCtH]|\d)")  # GCC 2.9x: Update__4CPedi, func_00123456__Fi


def unmangle(data: bytes, known) -> bytes:
    """A compiled object with its C++ names turned back into the game's own, so a function written as a class's
    method (`void CPed::func_00123456(int)`) is judged as, and calls, the game's func_00123456: GCC 2.9x's
    `func_00123456__4CPedi`, GCC 3's `_ZN4CPed13func_00123456Ei`. `known(name)` says whether a name is one of the
    game's. The new name is shorter, so it's written over the old one in the string table. GCC 2.9x's special names
    (_$_4CPed, a destructor; _vt$4CPed, a vtable) are the game's with _ for $ (consoles/ps2/symtab.py), as long."""
    if b"__" not in data and b"_Z" not in data and b"$" not in data:
        return data
    elf = ELFFile(BytesIO(data))
    symtab = elf.get_section_by_name(".symtab")
    if not isinstance(symtab, SymbolTableSection):
        return data
    base = elf.get_section(symtab["sh_link"])["sh_offset"]
    out = bytearray(data)
    for sym in symtab.iter_symbols():
        name = sym.name
        if not name or known(name):
            continue
        new = None
        m = _GCC2_MANGLED.match(name)
        if "$" in name and known(name.replace("$", "_")):
            new = name.replace("$", "_")
        elif m and known(m.group(1)):
            new = m.group(1)
        elif name.startswith("_Z"):  # <length><identifier>... (the Itanium C++ ABI)
            i = 2
            while i < len(name) and new is None:
                d = re.match(r"\d+", name[i:])
                if d is None:
                    i += 1
                    continue
                n, i = int(d.group()), i + len(d.group())
                if known(name[i:i + n]):
                    new = name[i:i + n]
                i += n
        if new:
            at = base + sym["st_name"]
            out[at:at + len(new) + 1] = new.encode() + b"\0"
    return bytes(out)
