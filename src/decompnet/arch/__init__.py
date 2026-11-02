"""The processors decompdecomp reads: one module each (mips.py, ppc.py, ppc64.py, x86.py, arm.py) with what depends on the
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

from . import (
    arm,
    mips,
    mips_debug,
    mips_fields,
    mips_pointer_lifetime,
    mips_regions,
    mips_source_recovery,
    mips_stack,
    mips_tools,
    ppc,
    ppc64,
    x86,
    x86_fields,
)

# What decompdecomp's readers can do with a processor's code (each is a module of its own that reads MIPS, and some
# x86):
COMPILER_MAP = "compiler_map"  # which compiler built each part of a game, from its habits (compilermap.py)
CLASSES = "classes"  # C++ classes from the vtables constructors store (classes.py; x86: from field_accesses' facts)
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
    # the float constants a diff row's instruction loads from data by address: text -> [(address, "f" | "d")], so
    # the diff can show their values (forkengine)
    float_operands: Callable[[str], list[tuple[int, str]]] | None = None
    build_tool_paths: Callable[[Any], list[Any]] | None = None
    # a decompiler's draft of a method shown as `Class::method`, with `this` (x86: Ghidra's, `this` in ecx):
    # (draft, function's address name, class, name, role, found from) -> the draft, or None (forkengine)
    method_draft: Callable[..., str | None] | None = None
    # variants of it capstone can't decode (Console.cpu_variant) -> their own reader: (code, big_endian) ->
    # [(offset, mnemonic, operands)], for scoring's diffs (MIPS: the PSP's Allegrex, read by rabbitizer)
    # Uniform saved-register/frame shifts in scored instruction pairs, for bounded padding hypotheses.
    stack_frame_growth: Callable[..., int | None] | None = None
    source_operand_changes: Callable[..., list] | None = None
    source_switch_blocks: Callable[..., dict] | None = None
    source_recovery_hints: Callable[..., list] | None = None
    source_extra_narrowing: Callable[..., list] | None = None
    source_private_abi: Callable[..., dict | None] | None = None
    source_pointer_lifetime: Callable[..., list] | None = None
    source_tail_branch: Callable[..., dict | None] | None = None
    stack_layout: Callable[..., dict] | None = None
    instruction_regions: Callable[..., dict | None] | None = None
    boundary_reason: Callable[..., str | None] | None = None
    variants: dict[str, Callable[[bytes, bool], list[tuple[int, str, str]]]] = field(default_factory=dict)

    object_functions: Callable[..., dict] | None = None  # explicit private procedure metadata missing from .symtab
    object_placeholders: Callable[..., dict] | None = None  # proven unused inline-return stubs


ARCHES = [
    Arch("mips", "MIPS", mips.CAPSTONE, mips.RELOC_MASKS, mips.asm_reason, True,
         frozenset({COMPILER_MAP, CLASSES, BOUNDARIES, STRUCTS}), mips_fields.read_facts,
         object_functions=mips_debug.functions, object_placeholders=mips_debug.placeholders, instruction_regions=mips_regions.read, build_tool_paths=mips_tools.build_paths, stack_frame_growth=mips_stack.frame_growth, stack_layout=mips_stack.layout, source_operand_changes=mips_source_recovery.operand_changes, source_extra_narrowing=mips_source_recovery.extra_narrowing, source_private_abi=mips_source_recovery.private_abi, source_pointer_lifetime=mips_pointer_lifetime.read, source_switch_blocks=mips_source_recovery.blocks, source_recovery_hints=mips_source_recovery.recovery_hints, source_tail_branch=mips_source_recovery.tail_branch, boundary_reason=mips.boundary_reason, variants={mips.ALLEGREX: mips.allegrex_lines}),
    Arch("ppc", "PowerPC", ppc.CAPSTONE, ppc.RELOC_MASKS, ppc.asm_reason),
    Arch("ppc64", "PowerPC64", ppc64.CAPSTONE, ppc64.RELOC_MASKS, ppc64.asm_reason, features=frozenset({BOUNDARIES})),
    Arch("x86", "x86", x86.CAPSTONE, x86.RELOC_MASKS, features=frozenset({CLASSES, STRUCTS}),
         field_accesses=x86_fields.read_facts, float_operands=x86.float_operands, method_draft=x86.method_draft),
    Arch("x86_64", "x86-64", x86.CAPSTONE_64, x86.RELOC_MASKS_64),
    Arch("arm", "ARM", arm.CAPSTONE, arm.RELOC_MASKS, arm.asm_reason, features=frozenset({BOUNDARIES})),
    Arch("thumb", "Thumb", arm.THUMB_CAPSTONE, arm.THUMB_RELOC_MASKS, arm.asm_reason,
         features=frozenset({BOUNDARIES})),
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
