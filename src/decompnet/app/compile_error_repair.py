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


_NOT_A_FUNCTION = ('Non-function name referenced', 'called object is not a function')


def call_through_cast(source: str, line: int) -> str | None:
    """`(sp4C + sp74)(sp6C);`: m2c called an address it computed. A compiler wants a function there, so the sum is cast
    to a function pointer first (once per statement on the named line)."""
    lines = source.split('\n')
    if not 0 < line <= len(lines):
        return None
    text = lines[line - 1]
    star = re.search(r'(^\s*|[=(,]\s*|return\s+)\*(\w+)\(', text)  # `*arg1();`: a call through the word's address
    if star:
        lines[line - 1] = f"{text[:star.start()]}{star[1]}((M2C_UNK (*)()){star[2]})({text[star.end():]}"
        return '\n'.join(lines)
    for found in re.finditer(r'(?:^\s*|[=(,]\s*|return\s+)(?=\()', text):
        open_at = found.end()
        close = _closing(text, open_at)
        if close is None or text[close + 1:close + 2] != '(':
            continue
        inner = text[open_at + 1:close]
        if re.match(r'\s*(?:\w+\s*\*+|\w+\s*\(\s*\*)', inner) or inner.lstrip().startswith('('):
            continue  # already a cast or a function-pointer type
        lines[line - 1] = f"{text[:open_at]}((M2C_UNK (*)()){text[open_at:close + 1]}){text[close + 1:]}"
        return '\n'.join(lines)
    return None


def null_as_zero(source: str, line: int) -> str | None:
    """"Constants must have arithmetic type" on `var = NULL;`: the variable is a number, so the null address is a zero."""
    lines = source.split('\n')
    if not 0 < line <= len(lines):
        return None
    fixed = re.sub(r'\bNULL\b|\(\(void \*\)\s*0\)', '0', lines[line - 1])
    if fixed == lines[line - 1]:
        return None
    lines[line - 1] = fixed
    return '\n'.join(lines)


def literal_store(source: str, line: int) -> str | None:
    """"This expression is not an lvalue" on `0 = value;`: m2c wrote a store through a null or constant base as a
    store to a number. It becomes a word store to that address, the way the code did it."""
    lines = source.split('\n')
    if not 0 < line <= len(lines):
        return None
    found = re.match(r'^(\s*)(0x[0-9A-Fa-f]+|\d+)\s*=\s*(.+?);\s*$', lines[line - 1])
    if not found:
        return None
    lines[line - 1] = f"{found[1]}*(s32 *){found[2]} = (s32)({found[3]});"
    return '\n'.join(lines)


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
        found = list(compile_error_repairs.diagnostics(result.log))
        for wording in ('Constants must have arithmetic type', 'This expression is not an lvalue'):
            found += [(int(n), wording) for n in re.findall(rf'line (\d+): {wording}', str(result.log))
                      if not any(line == int(n) for line, _ in found)]
        if best is None or len(found) < best:
            best, stalled = len(found), 0
        else:
            stalled += 1
        for line, message in sorted(found, reverse=True):
            key = (line, message)
            repaired = compile_error_repairs.repair(source, line, message, tried.get(key, 0))
            if repaired is None and any(part in message for part in _POINTER_FROM_FLOAT):
                repaired = undo_float_words(source, line)
            if repaired is None and 'Constants must have arithmetic type' in message:
                repaired = null_as_zero(source, line)
            if repaired is None and 'not an lvalue' in message:
                repaired = literal_store(source, line)
            if repaired is None and any(part in message for part in _NOT_A_FUNCTION):
                repaired = call_through_cast(source, line)
            tried[key] = tried.get(key, 0) + 1
            if repaired is not None and repaired != source:
                source, changed = repaired, True
        if not changed:
            return
