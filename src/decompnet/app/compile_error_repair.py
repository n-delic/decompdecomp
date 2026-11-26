"""Repair a machine draft that does not compile by following the compiler's own diagnostics.

m2c types addresses loosely, and a period compiler refuses what it writes (a pointer plus a pointer, an integer
dereferenced, a call through `*(table + i)`). decomprepair's compile_error_repairs changes the line a diagnostic names;
the compiler can't say which of several operators on that line is wrong, so each round asks for the next one on a line
that still fails. Bounded, so a draft that never compiles costs a handful of compiles and no more.
"""
import re

from decomprepair import compile_error_repairs

ROUNDS = 12  # without the number of errors dropping
MAX_ROUNDS = 60  # in all: a draft that keeps shedding errors one round at a time (a dozen wrong operators) gets to finish
FAMILY = 'compile_error_repair'


_POINTER_FROM_FLOAT = ('cannot convert to a pointer type', 'pointer value used where a floating point value')


def _closing(text: str, start: int) -> int | None:
    """The index of the ')' that closes the '(' at `start`."""
    depth = 0
    for k in range(start, len(text)):
        depth += (text[k] == '(') - (text[k] == ')')
        if depth == 0:
            return k
    return None


def undo_float_words(source: str, line: int) -> str | None:
    """GCC 2.x on a line that passes a float where the callee takes an address: the draft's float-word macros guessed
    the other way. `M2C_BITS_F32(x)` (an address read as a float) is the address again, and `M2C_BITWISE(T *, expr)`
    (a float read as an address) goes through a word. The callee's real prototype is not known here, so this is
    only tried on the lines the compiler named."""
    lines = source.split('\n')
    if not 0 < line <= len(lines):
        return None
    text = lines[line - 1]
    for macro in ('M2C_BITS_F32(', 'M2C_BITWISE('):
        at = text.find(macro)
        while at >= 0:
            close = _closing(text, at + len(macro) - 1)
            if close is None:
                break
            inner = text[at + len(macro):close]
            if macro == 'M2C_BITS_F32(':
                new = f'({inner})'
            else:
                kind, _, expr = inner.partition(',')
                if not re.fullmatch(r'\s*\w+\s*\*+\s*', kind):
                    at = text.find(macro, at + 1)
                    continue
                new = f'(({kind.strip()})(s32)({expr.strip()}))'
            lines[line - 1] = text[:at] + new + text[close + 1:]
            return '\n'.join(lines)
    return None


def candidates(compile_source, source, *, rounds=ROUNDS, check_cancel=None):
    """Yield (family, source) once, when `source` compiles after repairs. `compile_source(text)` returns something
    with `.ok` and `.log`; the draft itself is not offered again."""
    tried = {}
    best = None  # the fewest diagnostics seen: a round that lowers it earns the draft a fresh allowance
    stalled = 0
    for _ in range(MAX_ROUNDS):
        if stalled >= rounds:
            return
        if check_cancel:
            check_cancel()
        result = compile_source(source)
        if result.ok:
            if tried:
                yield FAMILY, source
            return
        if 'source too large' in str(result.log).lower():
            # the compiler takes at most compiler.MAX_SOURCE_BYTES: a big function's draft spends two fifths on comments and indentation
            smaller = compile_error_repairs.compact(source)
            if smaller is None:
                return
            source = smaller
            tried[('size', '')] = 1
            continue
        changed = False
        found = compile_error_repairs.diagnostics(result.log)
        if best is None or len(found) < best:
            best, stalled = len(found), 0
        else:
            stalled += 1
        for line, message in sorted(found, reverse=True):
            key = (line, message)
            repaired = compile_error_repairs.repair(source, line, message, tried.get(key, 0))
            if repaired is None and any(part in message for part in _POINTER_FROM_FLOAT):
                repaired = undo_float_words(source, line)
            tried[key] = tried.get(key, 0) + 1
            if repaired is not None and repaired != source:
                source, changed = repaired, True
        if not changed:
            return
