"""Repair a machine draft that does not compile by following the compiler's own diagnostics.

m2c types addresses loosely, and a period compiler refuses what it writes (a pointer plus a pointer, an integer
dereferenced, a call through `*(table + i)`). decomprepair's compile_error_repairs changes the line a diagnostic names;
the compiler can't say which of several operators on that line is wrong, so each round asks for the next one on a line
that still fails. Bounded, so a draft that never compiles costs a handful of compiles and no more.
"""
from decomprepair import compile_error_repairs

ROUNDS = 12
FAMILY = 'compile_error_repair'


def candidates(compile_source, source, *, rounds=ROUNDS, check_cancel=None):
    """Yield (family, source) once, when `source` compiles after repairs. `compile_source(text)` returns something
    with `.ok` and `.log`; the draft itself is not offered again."""
    tried = {}
    for _ in range(rounds):
        if check_cancel:
            check_cancel()
        result = compile_source(source)
        if result.ok:
            if tried:
                yield FAMILY, source
            return
        changed = False
        for line, message in sorted(compile_error_repairs.diagnostics(result.log), reverse=True):
            key = (line, message)
            repaired = compile_error_repairs.repair(source, line, message, tried.get(key, 0))
            tried[key] = tried.get(key, 0) + 1
            if repaired is not None and repaired != source:
                source, changed = repaired, True
        if not changed:
            return
