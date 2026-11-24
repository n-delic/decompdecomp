"""Whether a MIPS GCC function was built with another small-data threshold (`-G`), read from its diff.

A small N64 function of a game built with SN GCC 2.8.1 sat at 100 for hours. Its profile was `-G0 -mips2 -O2
-fno-schedule-insns`; it matched only with `-G4` and a declaration change, found by reading the compiler (`mips_check_split`).
GCC files a global at most `-G N` bytes in small data, which the assembler expands itself (`l.s $f12, G` becomes
`lui $at; lwc1 $f12, %lo(G)($at)`, and `lw $v1, G` becomes `lui $v1; lw $v1, %lo(G)($v1)`, the load's own register as the
temporary), and splits a bigger one in the compiler: an explicit `lui $r, %hi(G)` the optimiser may hoist, share and
allocate a register of its own (`lui $v0; lw $v1, %lo(G)($v0)`). At `-G0` every global is split, so the float the target
reached through `$at` was split in ours; at `-G4` a 4-byte float is not (it matched), and the integer global, which the
target still split, then needed to be bigger than the threshold: declared as a two-word array and read `G[0]` (decomp-repair
`extern_size_widen`). The pair scored 0; `-G4` with the scalar scored 110, `-G0` with the array 100. No statement order, qualifier
or literal spelling reaches it: whether a symbol is split is the profile's threshold against its declared size.

What names it is in the diff of the saved C: every differing row is a `lui`/load/store row the two sides write with other
registers, never another offset, operand or opcode (scheduler_fingerprints.order_rows_only), and one symbol's address is
built in `$at` or in the load's own register on one side (`lui $at, %hi(G)`) and in an explicit register on the other
(`lui $v0, %hi(G)` with the access through `%lo(G)($v0)`). The target is the one that tells which way to move the threshold:
target unsplit and ours split means a higher `-G` (the symbol fits under it), target split and ours unsplit a lower one
(or a bigger declaration).

The proposals are the profile's own flags with `-G` set to each other value of {0, 4, 8} (at most MAX_VARIANTS = 2 judge
compiles), the direction's first, and, for each, the saved C with its small externs widened (`widened`: the library's
`extern_size_widen`, whose threshold is the variant's). The judge decides; an exact result is never applied by itself, it is a
suggestion for the user's review (`is_small_data_variant`, the same gate `-mfp32`/`-mfp64` and the scheduling flags have).
The module reads flags and rows only; it never compiles or judges."""
from __future__ import annotations

import re
from collections import defaultdict

THRESHOLDS = (0, 4, 8)
MAX_VARIANTS = len(THRESHOLDS) - 1
MAX_WIDENED = 3  # widened sources judged with each variant
_LUI = re.compile(r'lui\s+\$(\w+)\s*,\s*%hi\(\s*([A-Za-z_]\w*)')
_ACCESS = re.compile(r'(\w+)\s+\$(\w+)\s*,\s*%lo\(\s*([A-Za-z_]\w*)[^)]*\)\(\$(\w+)\)')
_NOTE = re.compile(r'\s*;.*$')
_INTEGER_LOADS = {'lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld'}


def _flags(flags) -> list[str]:
    return flags.split() if isinstance(flags, str) else list(flags)


def threshold(flags) -> int | None:
    """The `-G` value the flags set (the last one named), None when they name none."""
    found = None
    flags = _flags(flags)
    for index, flag in enumerate(flags):
        if flag == '-G' and index + 1 < len(flags) and flags[index + 1].isdigit():
            found = int(flags[index + 1])
        elif re.fullmatch(r'-G\d+', flag):
            found = int(flag[2:])
    return found


def with_threshold(flags, value: int) -> list[str]:
    """The flags with `-G value` where the first `-G` stood (every other `-G` dropped); appended when there was none."""
    out, placed, skip = [], False, False
    flags = _flags(flags)
    for index, flag in enumerate(flags):
        if skip:
            skip = False
            continue
        if flag == '-G' and index + 1 < len(flags) and flags[index + 1].isdigit():
            skip = True
        elif not re.fullmatch(r'-G\d+', flag):
            out.append(flag)
            continue
        if not placed:
            out.append(f'-G{value}')
            placed = True
    return out if placed else [*out, f'-G{value}']


def _cells(rows, side: int) -> list[str]:
    return [_NOTE.sub('', row[side]).strip() for row in rows if row[side].strip()]


def forms(cells) -> dict[str, set[str]]:
    """{symbol: {how its address is built}}: `at` (`lui $at`), `dest` (the integer load's own register is the base: the
    assembler's expansion of a small global) or `split` (an explicit register, the compiler's own split)."""
    out: dict[str, set[str]] = defaultdict(set)
    for index, cell in enumerate(cells):
        hit = _LUI.match(cell)
        if not hit:
            continue
        register, symbol = hit[1], hit[2]
        if register == 'at':
            out[symbol].add('at')
            continue
        use = next((m for later in cells[index + 1:] if (m := _ACCESS.match(later)) and m[3] == symbol), None)
        if use is None or use[4] != register:
            continue
        out[symbol].add('dest' if use[1] in _INTEGER_LOADS and use[2] == use[4] else 'split')
    return out


def differences(rows) -> dict[str, str]:
    """{symbol: 'raise' or 'lower'} for each symbol one side builds unsplit (`at`, `dest`) and the other splits: `raise`
    when the target is the unsplit side (ours needs a higher `-G`), `lower` when it is the split one."""
    rows = [row for row in rows if isinstance(row, (list, tuple)) and len(row) == 3 and all(isinstance(c, str) for c in row)]
    target, ours = forms(_cells(rows, 1)), forms(_cells(rows, 2))
    out = {}
    for symbol in target.keys() & ours.keys():
        want, have = target[symbol], ours[symbol]
        if want == have:
            continue
        if 'split' in have and 'split' not in want:
            out[symbol] = 'raise'
        elif 'split' in want and 'split' not in have:
            out[symbol] = 'lower'
    return out


def variants(flags, rows) -> list[list[str]]:
    """The flags with each other threshold, the diff's direction first (a higher one first for `raise`, a lower one for
    `lower`; higher first when it shows both). Empty when the flags name no `-G` or the rows show no split difference."""
    from .scheduler_fingerprints import order_rows_only
    current = threshold(flags)
    if current is None or not rows or not order_rows_only(rows) or not (found := differences(rows)):
        return []
    others = [value for value in THRESHOLDS if value != current]
    up = sorted((v for v in others if v > current))
    down = sorted((v for v in others if v < current), reverse=True)
    order = [*down, *up] if set(found.values()) == {'lower'} else [*up, *down]
    return [with_threshold(flags, value) for value in order][:MAX_VARIANTS]


def why(own_flags, rows) -> str | None:
    """The reading that points to another threshold, or None. Needs the diff's rows."""
    if not variants(own_flags, rows):
        return None
    direction = set(differences(rows).values())
    return ('the saved C differs from the target only in how one symbol\'s address is built: the target ' + (
        'builds it in `$at` (small data) where ours splits it' if direction == {'raise'} else
        'splits it where ours does not' if direction == {'lower'} else 'and ours split it differently') +
        f', and the profile\'s -G{threshold(own_flags)} decides which globals are small')


def probes(own, rows) -> list[list[str]]:
    """The flags to judge the saved C under: `own`'s with another `-G`, when the diff points to it (a GCC only)."""
    from .. import toolchains
    if not own.get('toolchain') or own.get('cmd') or toolchains.compiler_family(own['toolchain']) != 'gcc':
        return []
    return variants(own['flags'], rows)


def widened(flags, source, name, header, asm) -> list[str]:
    """The saved C with its small externs the target splits declared as arrays above `flags`' threshold: the library's
    `extern_size_widen` children (at most MAX_WIDENED), none when it is not installed or has nothing to widen."""
    try:
        from decomprepair import extern_size_widen
        return [child for _, child in extern_size_widen.variants(source, name, header=header or '', asm=asm or '',
                                                                 flags=tuple(_flags(flags)))][:MAX_WIDENED]
    except Exception:  # noqa: BLE001 - the widening only adds trials: a library without it leaves the flags alone
        return []


def pairs(own, rows, source, name, header, asm) -> list[tuple[list[str], str]]:
    """[(flags, source)] to judge, per variant in the diff's direction order: the saved C, then each widened form of it
    (the likelier variant gets its combinations first when a budget is short)."""
    flag_sets = probes(own, rows)
    if not flag_sets or not source:
        return []
    out = []
    for flags in flag_sets:
        out += [(flags, source), *((flags, child) for child in widened(flags, source, name, header, asm))]
    return out


def is_small_data_variant(own_flags, flags) -> bool:
    """True when `flags` are `own_flags` with only the `-G` threshold changed: a trial the user must approve."""
    own_flags, flags = _flags(own_flags), _flags(flags)
    return (threshold(own_flags) is not None and threshold(flags) is not None and threshold(own_flags) != threshold(flags)
            and with_threshold(own_flags, 0) == with_threshold(flags, 0))
