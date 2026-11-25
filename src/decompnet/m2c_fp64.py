"""Assembly for m2c from a game whose FPU holds a double in one register (`-mfp64`, the N64's Status.FR=1).

m2c reads a double as an even/odd pair (`$f0` is `$f0:$f1`). Code built for the 64-bit register file loads a
double into any register, odd ones too (`ldc1 $f1, ...; mul.d $f0, $f0, $f1`), and keeps a single in `$f1` while
`$f0` holds a double. m2c then prints `(second half of f64)` in the C, which no compiler reads: 226 of a
2,465-function N64 game's open functions had no draft for that alone, and so nothing for the Deep Permuter to search.

`pair_free` renames registers, in one function, so that every double sits in an even register whose odd neighbour
nobody else uses. The values do not change, only the names m2c reads, and the C it prints never names a register.
A function with no double in an odd register (every `-mfp32` game, and all code without doubles) comes back
unchanged: there an odd register next to a double *is* its second half, and renaming it would break the pair.
The module reads assembly text only.
"""
import re

# The names the listing may use for $f0..$f31 (the N64 and PlayStation ABI's, then the plain numbers).
_ABI = ('fv0', 'fv0f', 'fv1', 'fv1f', 'ft0', 'ft0f', 'ft1', 'ft1f', 'ft2', 'ft2f', 'ft3', 'ft3f',
        'fa0', 'fa0f', 'fa1', 'fa1f', 'ft4', 'ft4f', 'ft5', 'ft5f',
        'fs0', 'fs0f', 'fs1', 'fs1f', 'fs2', 'fs2f', 'fs3', 'fs3f', 'fs4', 'fs4f', 'fs5', 'fs5f')
_NUMBER = {name: i for i, name in enumerate(_ABI)} | {f'f{i}': i for i in range(32)}
_REGISTER = re.compile(r'\$(f(?:[vats]\d+f?|\d+))\b')
_LINE = re.compile(r'^(?P<head>(?:\s*/\*.*?\*/)?\s*)(?P<op>[a-z][\w.]*)(?P<rest>\s+.*)?$')
# Registers m2c gives a meaning to (the result, the first two arguments): never a rename's target.
_SPECIAL = {0, 12, 14}
# Where a renamed value may go: temporaries first, then the registers a call preserves.
_POOL = (2, 4, 6, 8, 10, 16, 18, 20, 22, 24, 26, 28, 30)
_CONVERSIONS = ('cvt', 'trunc', 'round', 'ceil', 'floor')
_SINGLE_MOVES = ('lwc1', 'swc1', 'mtc1', 'mfc1')
_DOUBLE_MOVES = ('ldc1', 'sdc1', 'dmtc1', 'dmfc1')


def _roles(op: str, count: int) -> list[str] | None:
    """'d' (a double) or 's' (a single) for each floating-point operand of an instruction, in order."""
    if op in _DOUBLE_MOVES:
        return ['d']
    if op in _SINGLE_MOVES:
        return ['s']
    parts = op.split('.')
    if len(parts) == 3 and parts[0] in _CONVERSIONS:
        return ['d' if kind in 'dl' else 's' for kind in parts[1:]]
    if len(parts) >= 2 and parts[-1] in ('s', 'd', 'w', 'l'):
        return ['d' if parts[-1] in 'dl' else 's'] * count
    return None


def _operands(rest: str) -> list[str]:
    return [item.strip() for item in re.sub(r'#.*', '', rest or '').split(',') if item.strip()]


_GPR = ('zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
        's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra')
_MEMORY = re.compile(r'^(?:(%lo\(\s*(\w+)(?:\s*\+\s*(0x[0-9a-fA-F]+|\d+))?\s*\)|(-?(?:0x[0-9a-fA-F]+|\d+)))\()?\s*\$(\w+)\)?$')


def _memory_key(operand: str):
    """(base, symbol, offset) of `0x638($a0)` or `%lo(sym + 0x4)($at)`, so that two words can be compared."""
    match = _MEMORY.match(operand.replace(' ', ''))
    if not match:
        return None
    number = match[3] or match[4] or '0'
    return match[5], match[2], int(number, 0)


def _composed(moves: list[tuple[int, str, int, list[str]]]) -> set[int]:
    """Even registers whose odd neighbour is the other half of a double built or taken apart as two singles
    (`lwc1 $f0, 4(a0)` with `lwc1 $f1, 0(a0)`, `mtc1 $a2, $f12` with `mtc1 $a3, $f13`): m2c's own pairing."""
    found = set()
    for index, op, number, operands in moves:
        if number % 2 or not operands:
            continue
        for other_index, other_op, other, other_operands in moves:
            # (two moves with a conversion between them are two values: `mtc1 $v0, $f2; cvt.d.w $f2, $f2; mtc1 $v1, $f3`)
            if other_op != op or other != number + 1 or not other_operands or abs(other_index - index) > 1:
                continue
            if op in ('mtc1', 'mfc1'):
                names = [_GPR.index(o.lstrip('$')) if o.lstrip('$') in _GPR else -1 for o in (operands[0], other_operands[0])]
                if -1 not in names and abs(names[0] - names[1]) == 1:
                    found.add(number)
            else:
                a, b = _memory_key(operands[-1]), _memory_key(other_operands[-1])
                if a and b and a[:2] == b[:2] and abs(a[2] - b[2]) == 4:
                    found.add(number)
    return found


def _is_save(op: str, number: int, operands: list[str]) -> bool:
    """A callee-saved register stored to or loaded from the stack frame in the prologue or epilogue (`sdc1 $f20,
    0x20($sp)`): the whole register is kept, whatever the function holds in it, so it says nothing about a double
    or a single. Counting it made every saved register look like a double in use, and a function with a double in
    each had no free pair to rename into (5 of one game's functions kept `(second half of f64)`)."""
    return (op in _SINGLE_MOVES + _DOUBLE_MOVES and number >= 20 and bool(operands)
            and operands[-1].replace(' ', '').endswith('($sp)'))


def _scan(asm: str):
    """[line index] of floating-point instructions, the registers used as a double or as a single, and the even
    registers whose neighbour is the other half of a double (`_composed`)."""
    uses, doubles, singles, moves = [], set(), set(), []
    for index, line in enumerate(asm.splitlines()):
        match = _LINE.match(line)
        if not match:
            continue
        operands = _operands(match['rest'])
        floating = [o for o in operands if _REGISTER.fullmatch(o)]
        roles = _roles(match['op'], len(floating)) if floating else None
        if not roles:
            continue
        for register, role in zip(floating, roles):
            number = _NUMBER[_REGISTER.fullmatch(register)[1]]
            if _is_save(match['op'], number, operands):
                continue
            (doubles if role == 'd' else singles).add(number)
            if match['op'] in _SINGLE_MOVES:
                moves.append((index, match['op'], number, [o for o in operands if o != register]))
        uses.append(index)
    return uses, doubles, singles, _composed(moves)


def pair_free(asm: str) -> str:
    """`asm` with its doubles moved out of odd registers and away from a neighbour in use, or `asm` itself."""
    uses, doubles, singles, composed = _scan(asm)
    # An odd register that holds a double, or a single beside a double that is not made of two singles, only
    # exists in a 64-bit register file; otherwise an odd register is half of the even one before it.
    clash = {n for n in doubles if n % 2 == 0 and n + 1 in singles and n not in composed}
    # Two singles loaded from adjacent words into adjacent registers that no double operation ever reads are two
    # values, not one double; m2c's pairing would fuse them into an f64 load and print its second half.
    fused = composed - doubles
    if not clash and not fused and not any(number % 2 for number in doubles):
        return asm
    used = doubles | singles
    mapping = {}
    reserved = set()
    for number in sorted(n for n in doubles if n % 2 == 0):
        mapping[number] = number
        reserved |= {number, number + 1}
    free = [p for p in _POOL if p not in used and p + 1 not in used and p not in _SPECIAL]
    saved = [p for p in free if p >= 20]  # a callee-saved register stays one (m2c skips its save and restore)
    pairs = iter(free)
    saved_pairs = iter(saved)
    candidates = [p for p in (*range(1, 32, 2), *range(2, 32, 2))
                  if p not in used and p ^ 1 not in used and p not in reserved and p ^ 1 not in reserved
                  and p not in _SPECIAL]
    singles_free = iter(p for p in candidates if p < 20)
    saved_singles = iter(p for p in candidates if p >= 20)

    def place(number: int, double: bool) -> bool:
        if double and number >= 20:
            taken = next(saved_pairs, None)
            while taken is not None and taken in reserved:
                taken = next(saved_pairs, None)
        else:
            # (a single kept in a callee-saved register stays in one: in a temporary, m2c reads it as unset after a call)
            taken = next(pairs, None) if double else next(saved_singles if number >= 20 else singles_free, None)
            while double and taken is not None and taken in reserved:
                taken = next(pairs, None)
        if taken is None:
            return False
        mapping[number] = taken
        if double:
            reserved.update({taken, taken + 1})
        return True

    for number in sorted(doubles | singles):
        if number in mapping:
            continue
        if number in doubles:
            if not place(number, True):
                return asm
        elif number % 2 and (number - 1 in fused or (number - 1 in reserved and number - 1 not in composed)):
            if not place(number, False):
                return asm
        else:
            mapping[number] = number
    lines = asm.splitlines()
    for index in uses:
        lines[index] = _REGISTER.sub(lambda m: f'$f{mapping.get(_NUMBER[m[1]], _NUMBER[m[1]])}', lines[index])
    return '\n'.join(lines) + ('\n' if asm.endswith('\n') else '')
