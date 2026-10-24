"""A PSP game's other modules: the PRX files on its disc beside its program, which the game loads when it needs them
(a battle engine, a menu, a viewer). A PSP game with debug symbols keeps 32 of them, one bigger than its main
program, so a project of the main program alone left most of the game out.

Every plain module on the disc is found by what it is (an ELF for the Allegrex), wherever it lies and whatever it's
called. On the PSP they come and go, each relocated by the loader into whatever memory is free; here each gets
addresses of its own, the next free page after the main program and the modules before it, in the order of their
paths, so the same disc always splits the same way and no two modules share an address the judge links at. Where a
module lands changes nothing in another's code: a PRX's relocations name only its own segments, and it calls the
main program or another module through import stubs the loader links by NID (set aside like the main program's).

Laid out one after another, modules with large heaps in their .bss (a PSP game with debug symbols: four of 8 to 15
MB) reach past the PSP's memory; that's only addresses, but a module's code must stay below 0x10000000, the end of
the 256 MB its jumps (j, jal) reach from the main program's. A module that wouldn't is left out.

Also left out: the programs themselves and the system update (PSP_GAME/SYSDIR), a copy of the main program (a debug
build of it has its module name), and encrypted modules (~SCE, ~PSP: often Sony's own libraries, which a game carries
on its disc). The import log names what's left out and why.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from ...rom import RomError
from ..ps2.overlays import disc_files
from . import prx

PAGE = 0x1000  # each module starts on a page of its own
MAX_MODULE = 64 << 20
ENCRYPTED_MAGIC = (b"~PSP", b"~SCE")  # ~SCE: a signed header in front of ~PSP
PROGRAMS_DIR = "PSP_GAME/SYSDIR/"  # the game's programs (read_game takes them) and the system update
SHF_WRITE, SHF_EXECINSTR = 1, 4
JUMP_REGION_END = 0x10000000  # j and jal reach the 256 MB their own address is in: the main program's, from 0x08000000
ENCRYPTED, NO_ROOM = "encrypted", "no room below 0x10000000"


@dataclass
class Module:
    path: str  # where it is on the disc
    name: str  # its segment's name: the file's, as the PS2's overlays are named
    program: bytes  # laid out at its own addresses, as a fixed program
    small_data: bool = False  # whether it keeps data $gp-relative (prx.uses_small_data), read before it was laid out


def find(image, main: bytes) -> tuple[list[Module], list[tuple[str, str]]]:
    """(the disc's plain modules, each laid out after the main program `main` and the modules before it; (path, why)
    of each module left out). `image`: the disc (consoles/ps2/disc.py's _Disc)."""
    main_name = prx.module_name(main)
    end = prx.extent(main)
    found: list[Module] = []
    left_out: list[tuple[str, str]] = []
    names: set[str] = set()
    for path, lba, size in sorted(disc_files(image)):
        if path.startswith(PROGRAMS_DIR) or not prx.ELF_HEADER_BYTES < size <= MAX_MODULE:
            continue
        magic = image.read(lba, 4)
        if magic in ENCRYPTED_MAGIC:
            left_out.append((path, ENCRYPTED))
            continue
        if magic != prx.ELF_MAGIC:
            continue
        data = image.read(lba, size)
        try:
            if not prx.is_psp_elf(data) or main_name and prx.module_name(data) == main_name:  # (a debug build of it)
                continue
            base = -(-end // PAGE) * PAGE
            program = prx.relocate(data, base)
            segments = loaded(program)
            if min((vaddr for vaddr, _, _ in segments), default=0) < base:  # a fixed program whose addresses are taken
                continue
        except (RomError, struct.error, IndexError):  # a damaged module: the rest of the game still imports
            continue
        if any(vaddr + len(data) > JUMP_REGION_END for vaddr, data, _ in segments):
            left_out.append((path, NO_ROOM))
            continue
        end = prx.extent(program)
        name = re.sub(r"[^A-Za-z0-9_]", "_", Path(path).stem).strip("_").lower() or "module"
        while name in names:
            name += "_"
        names.add(name)
        found.append(Module(path, name, program, prx.uses_small_data(data)))
    return found, left_out


def left_out_lines(left_out: list[tuple[str, str]]) -> list[str]:
    """What the import log says of the modules left out, a line for each reason."""
    why = {ENCRYPTED: "encrypted: decompdecomp can't decrypt them (often they're Sony's own libraries, which a game "
                      "carries on its disc)",
           NO_ROOM: "no room for their code below 0x10000000, after the modules before them (their jumps couldn't "
                    "reach it)"}
    lines = []
    for reason, said in why.items():
        paths = [path for path, r in left_out if r == reason]
        if paths:
            lines.append(f"{len(paths)} module{'s' if len(paths) != 1 else ''} on the disc left out, {said}: "
                         + ", ".join(paths[:4]) + (", ..." if len(paths) > 4 else ""))
    return lines


def loaded(program: bytes) -> list[tuple[int, bytes, list[tuple[int, str]]]]:
    """(address, bytes, sections) of each loaded segment of a fixed program: its sections as (offset in the segment,
    splat's kind of subsegment), code as C, read-only data as rodata, the rest as data."""
    sections = [s for s in prx._sections(program) if s.flags & prx.SHF_ALLOC and s.kind != prx.SHT_NOBITS and s.size]
    out = []
    for kind, offset, vaddr, _, filesz, _, _, _ in prx._segments(program):
        if kind != prx.PT_LOAD or not filesz:
            continue
        parts = [(s.addr - vaddr, "c" if s.flags & SHF_EXECINSTR else "data" if s.flags & SHF_WRITE else "rodata")
                 for s in sorted(sections, key=lambda s: s.addr) if vaddr <= s.addr < vaddr + filesz]
        out.append((vaddr, program[offset:offset + filesz], parts))
    return out


def overlays(modules: list[Module]) -> list[tuple]:
    """The modules as the overlays splat's config gets (consoles/ps2/overlays.add_to_config): (name, bytes, address,
    0, sections) per loaded segment."""
    out = []
    for module in modules:
        segments = loaded(module.program)
        for k, (vaddr, data, parts) in enumerate(segments):
            out.append((f"{module.name}{k or ''}", data, vaddr, 0, parts))
    return out
