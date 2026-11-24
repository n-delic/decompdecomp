"""Whether a GCC function was built with another instruction-scheduling setting, read from its diff.

Three small vector-math near-matches of an N64 game built with SN GCC 2.8.1 sat at 50 for hours: hundreds of source
spellings, thousands of machine variants, hundreds of direct compiler runs. The cause was their address range's
profile, `-G0 -mips2 -O2 -fno-schedule-insns2`: the target's instruction order came from GCC's second scheduler, which
that flag turns off. The same saved C judged without the flag was exact. What named it: the diff of the best C had
only rows of the same instructions in another order (and the registers that follow from it), never another offset,
operand or opcode. No source spelling reaches it: where the compiler may move an instruction is the profile's.

Both passes are GCC's `-fschedule-insns` (before register allocation) and `-fschedule-insns2` (after it); `-O2`
turns both on, `-O1` neither. So, from the profile's own flags:

- one or both passes turned off with `-fno-...`: the same profile with that flag dropped (`-fno-schedule-insns2`
  alone first, then every scheduling flag);
- neither flag, `-O2` or higher: the profile with `-fno-schedule-insns2`, then with `-fno-schedule-insns`;
- neither flag, `-O1` or lower: the profile with `-fschedule-insns2`, then with both passes on.

That is at most `MAX_VARIANTS` judge compiles per function, tried only when the saved best's diff is order and
register rows (`order_rows_only`). The judge decides; an exact result is never applied by itself, it is a suggestion
for the user's review (`is_scheduler_variant`, the gate `-mfp32`/`-mfp64` and `-ffast-math` have).
The module reads flags and rows only; it never compiles or judges.
"""
from __future__ import annotations

import re
from collections import Counter

PASSES = ('-fschedule-insns', '-fschedule-insns2')
OFF = ('-fno-schedule-insns', '-fno-schedule-insns2')
SCHEDULING = frozenset(PASSES + OFF)
MAX_VARIANTS = 2
_REGISTER = re.compile(r'\$(?:f\d+|[vatsk]\d|zero|at|fp)\b')  # (not $sp, $gp or $ra: an offset from them is a meaning)
_NOTE = re.compile(r'\s*;.*$')  # what the judge adds to a row (`; target's D_00412340 = 0.5f`)
_BRANCH = re.compile(r'^(?:b\w*|j|jal)\b', re.I)
_NUMBER_LAST = re.compile(r'(?:,\s*|\s)(?:-?0x[0-9a-fA-F]+|-?\d+)$')


def _flags(flags) -> list[str]:
    return flags.split() if isinstance(flags, str) else list(flags)


def _level(flags) -> int | None:
    """The optimisation level the flags set (`-O2` is 2, `-O` is 1), None when they set none."""
    level = None
    for flag in flags:
        if flag == '-O':
            level = 1
        elif re.fullmatch(r'-O\d', flag):
            level = int(flag[2])
        elif flag == '-Os':
            level = 2
    return level


def variants(flags) -> list[list[str]]:
    """The flags with the other scheduling setting, at most MAX_VARIANTS, the likeliest first."""
    flags = _flags(flags)
    present = [f for f in flags if f in SCHEDULING]
    kept = [f for f in flags if f not in SCHEDULING]
    out: list[list[str]] = []
    if any(f in OFF for f in present) and not any(f in PASSES for f in present):
        dropped = [f for f in flags if f != '-fno-schedule-insns2']
        if '-fno-schedule-insns2' in flags:
            out.append(dropped)
        if kept != flags and kept not in out:
            out.append(kept)
    elif not present:
        if (_level(flags) or 0) >= 2:
            out += [[*flags, '-fno-schedule-insns2'], [*flags, '-fno-schedule-insns']]
        elif flags:
            out += [[*flags, '-fschedule-insns2'], [*flags, '-fschedule-insns', '-fschedule-insns2']]
    return out[:MAX_VARIANTS]


def _shape(cell: str) -> str:
    """An instruction as what it computes: registers and a branch's moving target left out."""
    cell = _NOTE.sub('', cell).strip()
    cell = _REGISTER.sub('$r', cell)
    if _BRANCH.match(cell):
        cell = _NUMBER_LAST.sub('', cell)
    return re.sub(r'\s+', ' ', cell)


def order_rows_only(rows) -> bool:
    """True when the diff has differing rows and the two sides hold the same instructions: only their order, and the
    registers that follow from it, differ (never an offset, an operand, another opcode or a missing instruction).
    A `nop` either side doesn't count: scheduling fills or leaves a delay slot."""
    rows = [row for row in rows if isinstance(row, (list, tuple)) and len(row) == 3
            and all(isinstance(cell, str) for cell in row)]
    if not any(marker != ' ' for marker, _, _ in rows):
        return False

    def bag(side):
        return Counter(shape for cell in (row[side] for row in rows) if cell.strip()
                       and (shape := _shape(cell)) != 'nop')
    return bag(1) == bag(2)


def why(own_flags, rows) -> str | None:
    """The reading that points to another scheduling setting, or None. Needs the diff's rows."""
    if rows and order_rows_only(rows) and variants(own_flags):
        return ('the saved C differs from the target only in the order of its instructions and the registers that '
                'follow: GCC\'s scheduling passes decide that order, and the profile sets them')
    return None


def probes(own, rows) -> list[list[str]]:
    """The flags to judge the saved C under, `own`'s with the other scheduling setting, when the diff points to it."""
    from .. import toolchains
    if not own.get('toolchain') or own.get('cmd') or why(own['flags'], rows) is None:
        return []
    # (GCC's flags; a toolchain with no declared family still counts when its profile already names a scheduling flag)
    if toolchains.compiler_family(own['toolchain']) != 'gcc' and not any(f in SCHEDULING for f in _flags(own['flags'])):
        return []
    return variants(own['flags'])


def is_scheduler_variant(own_flags, flags) -> bool:
    """True when `flags` are `own_flags` with only the scheduling flags changed: a trial the user must approve."""
    own_flags, flags = _flags(own_flags), _flags(flags)
    strip = lambda items: [f for f in items if f not in SCHEDULING]  # noqa: E731
    return strip(own_flags) == strip(flags) and [f for f in own_flags if f in SCHEDULING] != [
        f for f in flags if f in SCHEDULING]
