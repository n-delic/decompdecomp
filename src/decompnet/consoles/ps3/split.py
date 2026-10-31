"""Split a PlayStation 3 program into functions, and name the data they refer to.

No splitter exists for a linked PowerPC64 program (splat reads MIPS), so this one is the console's own, like the Game
Boy Advance's. Function starts, best source first:
1. the module descriptor at `e_entry` (a retail program lists its functions there, in link order), and the program's
   own function descriptors (`.opd`) where the section headers survive;
2. the targets of `bl` in the code (every call goes to a function);
3. a prologue after a return (`mflr r0`, `stdu r1,-n(r1)`, a register saved below the stack pointer: the first
   instructions of a function that makes calls or keeps registers), or any code after a plain return that no branch
   of the code before it jumps to (a leaf function).
Where the code ends comes from the executable sections when the headers say, else from the last function's return.

A function's assembly is capstone's, with calls and data named by the addresses its instructions refer to
(`func_00010200`, `D_00A12340`), which the judge (`arch/ppc64.py`) compares a candidate with. The bytes in each
line let a function be rebuilt from its stored assembly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import capstone

from ...arch.ppc64 import BCTR, BLR, CAPSTONE, NOP, R_PPC64_REL24, PpcTarget, scan_refs
from ...rom import RomError
from ...rom.split import DataSymbol, Segment, SplitResult
from . import elf as ps3elf

_CS = capstone.Cs(*CAPSTONE[True])
LISTS_FUNCTIONS = 16  # a descriptor table this long lists the program's functions (a homebrew one is a single entry)
MAX_DATA = 0x10000  # the size a data symbol is given at most (the program has no data sizes)
SECTION_EXEC = 4
_LINE = re.compile(r"^\s*/\* ([0-9A-F]{8}) ([0-9A-F]{8}) \*/\s+(.*?)\s*$")


@dataclass
class Ps3Function:
    """One function: its code, its assembly, and what its instructions refer to."""
    name: str
    vram: int
    rom: int
    code: bytes
    asm: str
    n_insns: int
    refs: list[tuple[int, int, int, int]] = field(default_factory=list)  # see arch.ppc64.PpcTarget.refs
    ptr: int = 4
    toc: int = 0
    segment: str = "ppu"
    asm_only: bool = False

    @property
    def size(self) -> int:
        return len(self.code)


# ------------------------------------------------------------------------------------------------------ assembly

def render_asm(name: str, vram: int, code: bytes, refs: list[tuple[int, int, int, int]],
               names: dict[int, str] | None = None) -> str:
    """capstone's listing of a function, one `/* address word */ instruction` per line (the word lets the function be
    rebuilt from this text), calls and data named by address (or by the program's own symbols, `names`), branches
    inside it by local labels."""
    names = names or {}
    by_insn = {r[0] & ~3: r for r in refs}
    targets: set[int] = set()
    insns = list(_CS.disasm(code, vram))
    for i in insns:
        if i.mnemonic.startswith("b") and i.mnemonic not in ("blr", "bctr", "bctrl", "blrl", "bl") and \
                i.op_str and re.fullmatch(r"(?:.*, )?0x[0-9a-f]+", i.op_str):
            dest = int(i.op_str.rsplit(" ", 1)[-1], 16)
            if vram <= dest < vram + len(code):
                targets.add(dest)
    lines = [f"glabel {name}"]
    for i in insns:
        if i.address in targets and i.address != vram:
            lines.append(f".L{i.address:08X}:")
        text = f"{i.mnemonic} {i.op_str}".strip()
        ref = by_insn.get(i.address - vram)
        if ref is not None and ref[1] == R_PPC64_REL24:
            text = f"{i.mnemonic} {names.get(ref[2] & 0xFFFFFFFF) or f'func_{ref[2] & 0xFFFFFFFF:08X}'}"
        elif i.mnemonic.startswith("b") and i.op_str:
            m = re.fullmatch(r"((?:.*, )?)0x([0-9a-f]+)", i.op_str)
            if m and int(m.group(2), 16) in targets:
                text = f"{i.mnemonic} {m.group(1)}.L{int(m.group(2), 16):08X}"
        elif ref is not None:
            text += f"  # {names.get(ref[2] & 0xFFFFFFFF) or f'D_{ref[2] & 0xFFFFFFFF:08X}'}"
        word = int.from_bytes(i.bytes, "big")
        lines.append(f"/* {i.address:08X} {word:08X} */\t{text}")
    return "\n".join(lines) + "\n"


def code_from_asm(text: str) -> tuple[int, bytes]:
    """(address, bytes) of the function a stored listing describes."""
    first, words = None, []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            first = int(m.group(1), 16) if first is None else first
            words.append(int(m.group(2), 16).to_bytes(4, "big"))
    return first or 0, b"".join(words)


# ----------------------------------------------------------------------------------------------- finding functions

def _words(program: bytes, offset: int, length: int) -> list[int]:
    return [int.from_bytes(program[offset + k:offset + k + 4], "big") for k in range(0, length - 3, 4)]


def _is_prologue(words: list[int], k: int) -> bool:
    """Whether the code at word `k` starts like a function that keeps a frame: within its first eight instructions
    `mflr r0`, `stdu r1,-n(r1)` or a register saved below the stack pointer (`std rN,-8(r1)`)."""
    if words[k] in (0, NOP):  # padding before a function is not its start
        return False
    for w in words[k:k + 8]:
        if w == 0x7C0802A6 or w & 0xFFFF0003 == 0xF8210001 or (w >> 26 == 62 and (w >> 16) & 31 == 1 and w & 0x8000):
            return True
        if w in (0, BLR, BCTR) or w >> 26 == 18:  # the end of something else
            return False
    return False


def _branch_targets(words: list[int], addr: int) -> set[int]:
    """Where the code's own `b` and `bc` instructions (not calls) jump."""
    out = set()
    for k, w in enumerate(words):
        if w >> 26 == 18 and not w & 3:
            li = w & 0x03FFFFFC
            out.add(addr + 4 * k + li - (0x04000000 if li & 0x02000000 else 0))
        elif w >> 26 == 16 and not w & 3:
            bd = w & 0xFFFC
            out.add(addr + 4 * k + bd - (0x10000 if bd & 0x8000 else 0))
    return out


def code_ranges(program: bytes, elf: ps3elf.Ps3Elf, descriptor=None) -> list[tuple[int, int, int]]:
    """(address, file offset, length) of what is code in the program: its executable sections when the headers say
    (the first segment's, as sections inside it), else the first segment up to the end of the last function the module
    descriptor lists, when it lists them (a retail program's section names are fragments, and the segment goes on with
    its read-only data); else the whole segment.
    shortcut: a function after the last listed one is missed, and the whole-segment case can show its read-only data
    after the last function as one more function."""
    code = elf.code
    out = []
    for s in elf.sections.values():  # (by name; or by flag, where decryption left the names zeros)
        if s.kind == 1 and code.vaddr <= s.addr < code.vaddr + code.filesz and s.size and \
                (s.name.startswith((".text", ".init", ".fini")) or s.name.startswith("#") and s.flags & ps3elf.EXECUTABLE):
            out.append((s.addr, s.offset, s.size))
    if out:
        return sorted(out)
    if descriptor is not None and descriptor.abi == "ilp32" and len(descriptor.functions) >= LISTS_FUNCTIONS:
        last = max(descriptor.functions)
        if code.vaddr <= last < code.vaddr + code.filesz:
            end = last + _last_end(program, code.offset + last - code.vaddr, code.vaddr + code.filesz - last)
            return [(code.vaddr, code.offset, end - code.vaddr)]
    skip = 0
    if code.offset == 0:  # the segment starts with the ELF header and the program headers
        skip = (ps3elf.ELF_HEADER_BYTES + 56 * len(elf.segments) + 3) & ~3
    return [(code.vaddr + skip, code.offset + skip, code.filesz - skip)]


def find_starts(program: bytes, elf: ps3elf.Ps3Elf, descriptor, log=print, named: set[int] = frozenset()) -> list[int]:
    """Every function start found, sorted: the descriptor's, the program's own `.opd`, the functions its symbols name
    (`named`), the targets of `bl`, and the prologues that follow a return."""
    ranges = code_ranges(program, elf, descriptor)
    lo, hi = min(r[0] for r in ranges), max(r[0] + r[2] for r in ranges)
    found: set[int] = set(descriptor.functions) if descriptor else set()
    found |= {a for a in named if a % 4 == 0 and any(r[0] <= a < r[0] + r[2] for r in ranges)}
    from_descriptor = len(found)

    def inside(addr: int) -> bool:
        return any(a <= addr < a + n for a, _, n in ranges)

    opd = elf.sections.get(".opd")
    if opd is not None and descriptor is not None:  # the free compilers' 24-byte descriptors: entry, TOC, environment
        step = 8 if descriptor.abi == "ilp32" else 24
        data = program[opd.offset:opd.offset + opd.size]
        for k in range(0, len(data) - step + 1, step):
            entry = int.from_bytes(data[k:k + (4 if step == 8 else 8)], "big")
            if inside(entry) and entry % 4 == 0:
                found.add(entry)
    from .imports import read_imports, stub_starts

    found |= stub_starts(program, ranges, {i.slot for i in read_imports(program)})  # (an import nothing calls too)
    from_headers = len(found) - from_descriptor
    for addr, off, length in ranges:
        words = _words(program, off, length)
        for k, w in enumerate(words):
            if w >> 26 == 18 and w & 1 and not w & 2:  # bl
                li = w & 0x03FFFFFC
                li -= 0x04000000 if li & 0x02000000 else 0
                dest = addr + 4 * k + li
                if addr <= dest < addr + length and dest % 4 == 0 and words[(dest - addr) // 4]:
                    found.add(dest)
    from_calls = len(found) - from_descriptor - from_headers
    for addr, off, length in ranges:
        words = _words(program, off, length)
        branched = _branch_targets(words, addr)
        ended = True  # the last instruction seen ended a function: a return, a jump through ctr, or a tail branch
        returned = True  # ... with a plain return
        for k, w in enumerate(words):
            if w in (0, NOP):  # (padding between functions doesn't change that)
                continue
            here = addr + 4 * k
            # a frame-keeping prologue after any end, or any code after a return that no branch of the code before
            # it jumps to (a leaf function: nothing else can lead there)
            if here not in found and ((ended and _is_prologue(words, k)) or (returned and here not in branched)):
                found.add(here)
            ended = w in (BLR, BCTR) or (w >> 26 == 18 and not w & 1)
            returned = w == BLR
    log(f"function starts: {from_descriptor} from the module descriptor, {from_headers} from the program's own "
        f"descriptors, {from_calls} from calls, {len(found) - from_descriptor - from_headers - from_calls} from "
        "prologues")
    return sorted(a for a in found if lo <= a < hi)


def _trim(code: bytes) -> bytes:
    """A function without the padding after its last return or tail branch (zero words and nops)."""
    words = _words(code, 0, len(code))
    end = len(words)
    while end > 1 and words[end - 1] in (0, NOP):
        end -= 1
    if end == len(words):
        return code
    last = words[end - 1]
    if last in (BLR, BCTR) or (last >> 26 == 18 and not last & 1):  # it ended there; what follows is padding
        return code[:4 * end]
    return code


def _last_end(program: bytes, offset: int, length: int) -> int:
    """Where the last function of a stretch of code with no end of its own ends: after the first return that is
    followed by zeros, or the stretch's end."""
    words = _words(program, offset, length)
    for k, w in enumerate(words):
        if w == BLR and (k + 1 == len(words) or words[k + 1] == 0):
            return 4 * (k + 1)
    return length


# ----------------------------------------------------------------------------------------------------- the split

def split(program: bytes, log=print, names: dict[int, str] | None = None,
          named: set[int] = frozenset()) -> tuple[list[Ps3Function], list[DataSymbol], ps3elf.Ps3Elf]:
    """The program's functions and data. `names`: the program's own symbols (address -> name), `named` the addresses
    of its functions among them (symbols.py): they name what the splitter finds, and a named function starts one."""
    names = names or {}
    elf = ps3elf.parse(program)
    descriptor = ps3elf.module_descriptor(program, elf)
    if descriptor is None:
        log("the program's entry point holds no function descriptor: its functions come from calls and prologues only")
    ptr = 4 if descriptor is None or descriptor.abi == "ilp32" else 8
    toc = descriptor.toc if descriptor else 0
    ranges = code_ranges(program, elf, descriptor)
    starts = find_starts(program, elf, descriptor, log, named)
    if not starts:
        raise RomError("found no functions in this PlayStation 3 program: is it the game's main executable (EBOOT.ELF)?")

    def inside_code(addr: int) -> bool:
        return any(a <= addr < a + n for a, _, n in ranges)

    def inside(addr: int) -> bool:
        return any(s.vaddr <= addr < s.vaddr + s.memsz for s in elf.loads)

    def read(addr: int, n: int) -> bytes | None:
        return ps3elf.read_at(program, elf, addr, n)

    tocs = dict(zip(descriptor.functions, descriptor.tocs)) if descriptor else {}
    unit_of: dict[int, str] = {}
    if len(set(tocs.values())) > 1:  # link units: runs of functions that share a TOC base
        unit, last = -1, None
        for func in sorted(tocs):
            if tocs[func] != last:
                unit, last = unit + 1, tocs[func]
            unit_of[func] = f"unit_{unit:04d}"
    functions: list[Ps3Function] = []
    unit = "ppu"
    for k, start in enumerate(starts):
        r_addr, r_off, r_len = next(r for r in ranges if r[0] <= start < r[0] + r[2])
        r_end = r_addr + r_len
        offset = r_off + start - r_addr
        following = starts[k + 1] if k + 1 < len(starts) and starts[k + 1] < r_end else \
            start + _last_end(program, offset, r_end - start)
        code = _trim(program[offset:offset + following - start])
        if not code:
            continue
        unit = unit_of.get(start, unit)
        refs = scan_refs(code, start, tocs.get(start, toc), ptr, read, inside)
        name = names.get(start) or f"func_{start:08X}"
        functions.append(Ps3Function(name, start, offset, code, render_asm(name, start, code, refs, names), len(code) // 4,
                                     refs, ptr, tocs.get(start, toc), unit))
    log(f"found {len(functions)} functions")
    return functions, data_symbols(program, elf, functions, inside_code, names), elf


def data_symbols(program: bytes, elf: ps3elf.Ps3Elf, functions: list[Ps3Function], inside_code,
                 names: dict[int, str] | None = None) -> list[DataSymbol]:
    """A symbol for every address the functions' instructions refer to outside the code (a datum, a string, a TOC
    slot's target), sized to the next one: the program has no data sizes, so these are labels more than objects."""
    addrs: set[int] = set()
    fn_addrs = {f.vram for f in functions}
    for f in functions:
        for _, kind, address, _slot in f.refs:
            if kind != R_PPC64_REL24 and address not in fn_addrs and not (f.vram <= address < f.vram + f.size):
                addrs.add(address & 0xFFFFFFFF)
    ordered = sorted(addrs)
    out = []
    for k, addr in enumerate(ordered):
        seg = next((s for s in elf.loads if s.vaddr <= addr < s.vaddr + s.memsz), None)
        if seg is None:
            continue
        end = min(ordered[k + 1] if k + 1 < len(ordered) else 1 << 40, seg.vaddr + seg.memsz, addr + MAX_DATA)
        size = max(1, end - addr)
        rom = None if addr - seg.vaddr >= seg.filesz else seg.offset + addr - seg.vaddr
        kind = "bss" if rom is None else "data"
        if rom is not None:
            head = program[rom:rom + min(size, 64)]
            nul = head.find(b"\0")
            if nul > 1 and all(32 <= c < 127 or c in (9, 10, 13) for c in head[:nul]):
                kind, size = "string", nul + 1
        label = (names or {}).get(addr) or f"{'STR' if kind == 'string' else 'B' if kind == 'bss' else 'D'}_{addr:08X}"
        out.append(DataSymbol(label, addr, rom, size, kind))
    return out


def split_result(program: bytes, log=print, symbols: list[str] | None = None) -> SplitResult:
    from . import symbols as own

    names, named = own.from_lines(symbols)
    functions, data, elf = split(program, log=log, names=names, named=named)
    segments = [Segment(f"seg{k}", s.offset, s.offset + s.filesz, s.vaddr) for k, s in enumerate(elf.loads) if s.filesz]
    return SplitResult(functions, data, segments, {}, None, "", "", "ps3", 0, "")


# ------------------------------------------------------------------------------------------- stored functions

def function_from_row(row) -> Ps3Function:
    """A stored function row as the splitter's function object."""
    import json

    row = dict(row)
    meta = json.loads(row.get("meta") or "{}")
    refs = PpcTarget.refs_from_json(json.loads(row.get("relocs") or "[]"), meta.get("slots"))
    code = row.get("target") or b""
    return Ps3Function(row["name"], row["vram"], row.get("rom_offset") or 0, code, row.get("asm") or "",
                       row.get("n_insns") or max(1, len(code) // 4), refs, meta.get("ptr", 4), meta.get("toc", 0),
                       row.get("unit") or "ppu")


def parse_function(name: str, text: str, segment: str) -> Ps3Function:
    """A function from the listing a project stored (its words are in the lines)."""
    vram, code = code_from_asm(text)
    return Ps3Function(name, vram, 0, code, text, max(1, len(code) // 4), [], segment=segment)


def _cut(fn: Ps3Function, name: str, vram: int, rom: int, code: bytes, refs, shift: int) -> Ps3Function:
    refs = [(r[0] - shift, *r[1:]) for r in refs if shift <= r[0] < shift + len(code)]
    return Ps3Function(name, vram, rom, code, render_asm(name, vram, code, refs), max(1, len(code) // 4), refs,
                       fn.ptr, fn.toc, fn.segment)


def split_function(row, at: int, new_name: str) -> tuple[Ps3Function, Ps3Function]:
    """Cut a stored function at `at`: the head keeps the name, the tail starts at `at` (a 4-byte boundary)."""
    fn = function_from_row(row)
    off = at - fn.vram
    if not 0 < off < len(fn.code) or off % 4:
        raise ValueError(f"0x{at:08X} isn't an instruction boundary inside {fn.name}")
    head = _cut(fn, fn.name, fn.vram, fn.rom, fn.code[:off], fn.refs, 0)
    tail = _cut(fn, new_name, at, fn.rom + off, fn.code[off:], fn.refs, off)
    return head, tail


def function_at(rom: bytes, addr: int, limit: int, name: str) -> Ps3Function | None:
    """A function the splitter missed at `addr`: the code there to its first return (`rom` is the program itself)."""
    try:
        elf = ps3elf.parse(rom)
    except RomError:
        return None
    seg = next((s for s in elf.loads if s.vaddr <= addr < s.vaddr + s.filesz), None)
    if seg is None or addr % 4:
        return None
    off = seg.offset + addr - seg.vaddr
    room = min(limit, seg.vaddr + seg.filesz) - addr
    if room < 4 or not _words(rom, off, 4)[0]:
        return None
    descriptor = ps3elf.module_descriptor(rom, elf)
    ptr = 4 if descriptor is None or descriptor.abi == "ilp32" else 8
    code = _trim(rom[off:off + _last_end(rom, off, room)])
    toc = descriptor.toc if descriptor else 0

    def inside(a: int) -> bool:
        return any(s.vaddr <= a < s.vaddr + s.memsz for s in elf.loads)

    refs = scan_refs(code, addr, toc, ptr, lambda a, n: ps3elf.read_at(rom, elf, a, n), inside)
    return Ps3Function(name, addr, off, code, render_asm(name, addr, code, refs), max(1, len(code) // 4), refs, ptr, toc)


def join_saved_functions(rows: list[dict], start: int, end: int, read) -> Ps3Function:
    """Join saved pieces back into one function, filling only zero padding from the game's bytes."""
    rows = [dict(row) for row in rows]
    first = function_from_row(rows[0])
    pieces, refs, pos = [], [], start
    for row in rows:
        code, addr = row.get("target") or b"", row["vram"]
        if addr > pos:
            gap = read(pos, addr - pos)
            if gap is None or gap.strip(b"\0"):
                raise ValueError("the saved layout crosses a gap in this analysis")
            pieces.append(gap)
        pieces.append(code)
        refs += [(r[0] + addr - start, *r[1:]) for r in function_from_row(row).refs]
        pos = addr + len(code)
    if pos < end:
        gap = read(pos, end - pos)
        if gap is None or gap.strip(b"\0"):
            raise ValueError("the saved layout crosses a gap in this analysis")
        pieces.append(gap)
    code = b"".join(pieces)[:end - start]
    return Ps3Function(first.name, start, first.rom, code, render_asm(first.name, start, code, refs),
                       max(1, len(code) // 4), refs, first.ptr, first.toc, first.segment)
