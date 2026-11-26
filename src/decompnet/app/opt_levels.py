"""Each -O level of a compiler, as flag sets that change nothing else: what the profile probe judges for every function.

A game built `-O2` by default had about 355 of its 820 matched functions at `-O1`, and dozens of "stuck" open ones
turned out to be among them, so the level is a thing to try for every function, not only where something points at it.
The levels of a compiler family are the ones flag search knows (engine/compilers.FLAG_LEVELS), plus `-O0`.
"""
from __future__ import annotations

import re

from .. import toolchains

LEVEL = re.compile(r'-O(?:\d|s|g)?(,[ps])?')  # (CodeWarrior writes -O4,p: the suffix stays)
LEVELS_MAX = 5  # the most other levels a family has: what an allowance for them has to hold


def family(toolchain: str) -> str:
    from .engine.compilers import FLAG_FAMILIES
    return toolchains.compiler_family(toolchain) or next((f for prefix, f in FLAG_FAMILIES if toolchain.startswith(prefix)), 'gcc')


def levels(toolchain: str) -> tuple[str, ...]:
    """The compiler's levels, or () for a family flag search lists none for."""
    from .engine.compilers import FLAG_LEVELS
    known = FLAG_LEVELS.get(family(toolchain), ())
    return tuple(dict.fromkeys(('-O0', *known))) if known else ()


def variants(toolchain: str, flags) -> list[list[str]]:
    """`flags` at each other level of the compiler: the level is replaced where it stands (added when the flags name
    none) and every other flag is kept."""
    flags = flags.split() if isinstance(flags, str) else list(flags)
    current = next((f for f in reversed(flags) if LEVEL.fullmatch(f)), None)
    suffix = (LEVEL.fullmatch(current)[1] or '') if current else ''
    out = []
    for level in levels(toolchain):
        if current and current.split(',')[0] == level:
            continue
        new = level + suffix if level.startswith('-O') else level  # (IDO's -g stands where its level did)
        changed = [new if LEVEL.fullmatch(f) else f for f in flags] if current else [*flags, new]
        if changed != flags and changed not in out:
            out.append(changed)
    return out
