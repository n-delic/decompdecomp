"""A PS3 program's imports and kernel calls: which of its functions are the loader's stubs and the kernel's, and their names.

The loader links a program to the system's libraries by name and NID. The program says where in two places:
- `.sys_proc_prx_param` (a program header of type 0x60000002, or the section): a 0x28 or 0x40 byte block with the magic
  0x1b434cec and the start and end of the library tables (`.lib.ent` exports, `.lib.stub` imports);
- each import library's record in `.lib.stub` (0x2c bytes, 32-bit fields): the function count (u16 at +6), the
  library's name, its NIDs (one u32 each) and its stub slots (one u32 each, which the loader fills with the address of
  the function it linked) as three addresses at +0x10, +0x14, +0x18.

A call goes to a small function of the SDK's, which loads its slot and jumps through it:
    lis r12,slot@ha; lwz r12,slot@l(r12); lwz r0,0(r12); lwz r2,4(r12); mtctr r0; bctr(l)
so the slot an import stub loads says which import it is (stub_slot). A kernel call is `li r11,N; sc; blr` (syscall_number):
the number is the table's (nids.syscalls). Names: `<library>_<function>` from PSL1GHT's lists (nids.py), else
`<library>_<NID>`, the way the PSP's stubs are named.
A retail game's SDK stubs build the slot's address as `li r12,0; oris r12,r12,hi` and save the TOC first
(`std r2,0x28(r1)`): stub_slot follows any of those. shortcut: stubs that reach their slot through the TOC
(`lwz r12,off(r2)`) rather than by address aren't recognised; none were in the retail game checked.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import elf as ps3elf
from . import nids

PT_PROC_PRX_PARAM = 0x60000002
PRX_PARAM_MAGIC = 0x1B434CEC
RECORD_BYTES = 0x2C
MAX_STUB_INSNS = 24
STUB_BYTES = 32  # an SDK import stub: 8 instructions
MAX_SYSCALL_INSNS = 8
LIS, LWZ, LD, LI, SC, ORI, ORIS = 15, 32, 58, 14, 17, 24, 25


@dataclass
class Import:
    library: str
    nid: int
    slot: int  # the address of the import's stub slot

    @property
    def name(self) -> str:
        return nids.name(self.library, self.nid) or f"{self.nid:08X}"

    @property
    def symbol(self) -> str:
        return f"{self.library}_{self.name}"


def _param(data: bytes, elf: ps3elf.Ps3Elf) -> tuple[int, int] | None:
    """(start, end) of the import tables from the program's process parameter block."""
    section = elf.sections.get(".sys_proc_prx_param")
    if section is not None:
        block = ps3elf.section_bytes(data, section)
    else:
        seg = next((s for s in elf.segments if s.kind == PT_PROC_PRX_PARAM), None)
        block = data[seg.offset:seg.offset + min(seg.filesz, 0x40)] if seg else b""
    if len(block) < 0x20:
        return None
    _, magic, _, _, _, _, start, end = struct.unpack_from(">8I", block)
    return (start, end) if magic == PRX_PARAM_MAGIC and start < end else None


def read_imports(data: bytes) -> list[Import]:
    """Every function the program imports: its library, NID and stub slot. Empty when it has no import tables this
    reader knows (a homebrew program the free linker made has none)."""
    try:
        elf = ps3elf.parse(data)
    except Exception:  # not a PS3 program: nothing imported
        return []
    table = _param(data, elf)
    if table is None and ".lib.stub" in elf.sections:
        s = elf.sections[".lib.stub"]
        table = (s.addr, s.addr + s.size)
    if table is None:
        return []
    out: list[Import] = []
    at = table[0]
    while at + RECORD_BYTES <= table[1]:
        record = ps3elf.read_at(data, elf, at, RECORD_BYTES)
        if record is None or record[0] < RECORD_BYTES:
            break
        count, = struct.unpack_from(">H", record, 6)
        name_at, nids_at, slots_at = struct.unpack_from(">3I", record, 0x10)
        label = (ps3elf.read_at(data, elf, name_at, 48) or b"").split(b"\0", 1)[0]
        listed = ps3elf.read_at(data, elf, nids_at, 4 * count) if count else b""
        if not label or not label.isascii() or listed is None or len(listed) != 4 * count:
            break  # not a record as the loader reads them: names from here on would be wrong
        out += [Import(label.decode(), struct.unpack_from(">I", listed, 4 * k)[0], slots_at + 4 * k) for k in range(count)]
        at += record[0]
    return out


def _words(code: bytes):
    return [int.from_bytes(code[k:k + 4], "big") for k in range(0, len(code) - 3, 4)]


def stub_slot(code: bytes, slots: set[int]) -> int | None:
    """The stub slot an import stub's code loads, or None when it isn't a short function that loads one: the slot's
    address is built in a register (`lis rA,hi`, or `li rA,0; oris rA,rA,hi` as a retail game's SDK stubs do; then
    `addi`/`ori` for the low half, or the load's own offset) and a word or doubleword is loaded from it."""
    words = _words(code)
    if not 2 <= len(words) <= MAX_STUB_INSNS:
        return None
    known: dict[int, int] = {}  # register -> the address it holds so far

    def signed(v: int) -> int:
        return v - 0x10000 if v & 0x8000 else v
    for w in words:
        op, d, a, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op in (LI, LIS) and (a == 0 or a in known):  # addi / addis (li / lis when rA is 0)
            known[d] = ((known.get(a, 0) if a else 0) + (signed(imm) << (16 if op == LIS else 0))) & 0xFFFFFFFF
        elif op in (ORI, ORIS) and d in known:  # (d is the source, a the destination here)
            known[a] = known[d] | (imm << (16 if op == ORIS else 0))
        elif op in (LWZ, LD) and a in known:
            address = (known[a] + signed(imm & (0xFFFF if op == LWZ else 0xFFFC))) & 0xFFFFFFFF
            if address in slots:
                return address
            known.pop(d, None)
        elif d in known and op not in (36, 62):  # anything else that writes the register (but stores) forgets it
            known.pop(d, None)
    return None


def stub_starts(program: bytes, ranges: list[tuple[int, int, int]], slots: set[int]) -> set[int]:
    """Where the import stubs are in the code `ranges` ((address, file offset, length)): each starts building its
    slot's address in r12 (`li r12,0` or `lis r12,hi`). The splitter starts a function at each, as no call needs to
    reach one (an import the game never calls still has its stub)."""
    out = set()
    for addr, off, length in ranges:
        for k in range(0, length - 4, 4):  # (to the range's end: the table's last stub is in its last 32 bytes)
            w = int.from_bytes(program[off + k:off + k + 4], "big")
            if (w == 0x39800000 or w >> 16 == 0x3D80) and \
                    stub_slot(program[off + k:off + min(k + STUB_BYTES, length)], slots) is not None:
                out.add(addr + k)
    return out


def syscall_number(code: bytes) -> int | None:
    """The kernel call a short function makes (`li r11,N` before an `sc`), or None."""
    words = _words(code)
    if not 2 <= len(words) <= MAX_SYSCALL_INSNS or not any(w >> 26 == SC and w & 2 for w in words):
        return None
    number = None
    for w in words:
        if w >> 26 == LI and (w >> 21) & 31 == 11 and (w >> 16) & 31 == 0:
            number = w & 0xFFFF
        elif w >> 26 == SC and w & 2:
            return number
    return None


def names(functions: list[tuple[str, bytes]], imports: list[Import], taken: set[str]) -> tuple[list[tuple[str, str]], set[str]]:
    """((old name, new name) for each import stub and kernel call among (name, code) that still has a generated name
    (func_...), the old names of the import stubs). A second stub of one import gets _2, and a name the game already
    uses is left alone."""
    by_slot = {i.slot: i for i in imports}
    calls = nids.syscalls()
    renames, stubs, used = [], set(), set(taken)
    for name, code in functions:
        if not name.startswith("func_"):
            continue
        code = code or b""
        slot = stub_slot(code, set(by_slot))
        number = syscall_number(code) if slot is None else None
        label = by_slot[slot].symbol if slot is not None else calls.get(number) if number is not None else None
        if label is None:
            continue
        new, copy = label, 2
        while new in used:
            new, copy = f"{label}_{copy}", copy + 1
        used.add(new)
        renames.append((name, new))
        if slot is not None:
            stubs.add(name)
    return renames, stubs
