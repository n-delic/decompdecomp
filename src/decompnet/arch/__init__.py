"""The processors decompdecomp reads: one module each (mips.py, ppc.py, x86.py, arm.py) with what depends on the
processor alone, whatever the console: the relocations a match ignores the bits of, how capstone disassembles it,
how to tell code no compiler makes, and which of decompdecomp's readers exist for it.

A console names its processor (consoles/: `arch = "mips"`). The shared code asks the processor here instead of
comparing names: a processor without a reader for a feature (the compiler map, say) leaves that feature out, so
the feature shows as missing on that console, not as an `if` somewhere in the engine.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import arm, mips, mips_fields, ppc, x86

# What decompdecomp's readers can do with a processor's code (each is a module of its own that reads MIPS today):
COMPILER_MAP = "compiler_map"  # which compiler built each part of a game, from its habits (compilermap.py)
CLASSES = "classes"  # C++ classes from the vtables constructors store (classes.py)
BOUNDARIES = "boundaries"  # cutting a function where an agent says it really ends (engine/editing.py, rom/split.py)
STRUCTS = "structs"  # struct layouts from the loads and stores through pointers (structs.py, Arch.field_accesses)


@dataclass(frozen=True)
class Arch:
    id: str  # the name ELF objects and the projects table use: "mips", "ppc", "x86", "arm"
    name: str
    capstone: dict[bool, tuple[int, int]]  # big-endian? -> capstone's (architecture, mode)
    reloc_masks: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # see elfobj.mask_relocs
    # why a function's code can't come from a compiler, or None: (code, big_endian, **the console's facts)
    asm_reason: Callable[..., str | None] | None = None
    # its asm_reason needs the console's facts (MIPS: which registers carry arguments depends on the console's ABI,
    # the PS2's EABI passes them in t0-t3 too), so code whose console isn't known (a fork's object) isn't judged
    asm_needs_console: bool = False
    features: frozenset[str] = frozenset()
    # what a function does with pointers, for structs.py: (vram, code, relocs, big_endian=, datum=) -> structs.Facts
    field_accesses: Callable[..., Any] | None = None


ARCHES = [
    Arch("mips", "MIPS", mips.CAPSTONE, mips.RELOC_MASKS, mips.asm_reason, True,
         frozenset({COMPILER_MAP, CLASSES, BOUNDARIES, STRUCTS}), mips_fields.read_facts),
    Arch("ppc", "PowerPC", ppc.CAPSTONE, ppc.RELOC_MASKS, ppc.asm_reason),
    Arch("x86", "x86", x86.CAPSTONE, x86.RELOC_MASKS),
    Arch("x86_64", "x86-64", x86.CAPSTONE_64, x86.RELOC_MASKS_64),
    Arch("arm", "ARM", arm.CAPSTONE, arm.RELOC_MASKS, arm.asm_reason),
    Arch("thumb", "Thumb", arm.THUMB_CAPSTONE, arm.THUMB_RELOC_MASKS, arm.asm_reason),
]
_BY_ID = {a.id: a for a in ARCHES}
UNKNOWN = Arch("", "an unknown processor", {})


def get(arch_id: str | None) -> Arch:
    """The processor by its id; one decompdecomp has no reader for (a public decomp's) has no features."""
    return _BY_ID.get(arch_id or "", UNKNOWN)


def reloc_masks() -> dict[tuple[str, int], tuple[int, int, int]]:
    """(arch, relocation type) -> (byte offset from r_offset, length, bitmask to clear), for every processor."""
    return {(a.id, kind): mask for a in ARCHES for kind, mask in a.reloc_masks.items()}


def capstone_modes() -> dict[tuple[str, bool], tuple[int, int]]:
    """(arch, big-endian) -> capstone's (architecture, mode), for every processor."""
    return {(a.id, big): mode for a in ARCHES for big, mode in a.capstone.items()}
