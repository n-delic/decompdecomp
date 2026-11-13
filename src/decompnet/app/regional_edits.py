"""The other release's C edited where the two releases' code differs: a constant, an operator, a branch's condition,
a load's width, a call one release lacks.

What Try its code can't match after fitting (regional_adapt) is a change between the releases. On a Europe/North
America N64 pair, 33 of 298 such functions differed by one kind of change and 52 by two: a changed constant, `+`
where the other has `-`, `<` for `<=`, a u16 that became an s32, a call dropped or added. Each differing instruction
of the closest version is traced to its C line (source_lines, the compiler's own line table), and the edits that
instruction asks for (the processor's Arch.edit_hint) are tried on that line. The search keeps the edit that brings
the score down most, then looks again from there, a few rounds; the judge checks every candidate, so an edit that
doesn't help costs a compile and nothing else. The register and order shuffling that's left is the permuter's.
"""

from __future__ import annotations

import re

from .. import arch as processors
from .. import scoring
from ..network import NetworkError
from .regional_adapt import C_INTEGER, NUMBER, _code_only

ROUNDS = 4  # edits combined at most: each round keeps the best one and looks again
PER_ROUND = 24  # candidates judged a round, at most
MAX_TIES = 2  # sources as close as the best that go to the judge as well
MAX_SHARE = 0.4  # a start further off than this share of the function (its diff score) isn't a few edits away

TYPES = {(1, True): 's8', (1, False): 'u8', (2, True): 's16', (2, False): 'u16', (4, True): 's32', (4, False): 'u32'}
TYPE_WORD = re.compile(r'\b(?:unsigned\s+char|signed\s+char|unsigned\s+short|unsigned\s+int|unsigned\s+long|'
                       r'u8|s8|u16|s16|u32|s32|char|short|int|long)\b')
COMPARE = re.compile(r'==|!=|<=|>=|(?<![<>-])<(?![<=])|(?<![<>-])>(?![>=])')
FLIPS = {'==': ('!=',), '!=': ('==',), '<': ('>=', '<='), '>=': ('<', '>'), '>': ('<=', '>='), '<=': ('>', '<')}
OPERATOR = {
    '+': r'(?<![+=])\+(?![+=])', '-': r'(?<![-=])-(?![-=>])', '&': r'(?<![&])&(?![&])', '|': r'(?<!\|)\|(?!\|)',
    '^': r'\^', '<<': r'<<', '>>': r'>>', '*': r'(?<=[\w)\]])\s*\*(?=\s*[\w(])', '/': r'(?<![/*])/(?![/*])',
}
SIGN = {'u8': 's8', 's8': 'u8', 'u16': 's16', 's16': 'u16', 'u32': 's32', 's32': 'u32'}


def judge(net, proj, header, source, fn):
    """(score, diff rows, compiled object) for one candidate, or None when it doesn't build or lacks the function."""
    try:
        result = net._compile(proj, header, source, fn)
    except NetworkError:
        return None
    if not result.ok or not result.obj:
        return None
    cand, _, _ = net._evaluate(fn, proj, result.obj)
    if cand is None:
        return None
    d = scoring.diff(net._target_obj(fn, proj), cand)
    return d.score, d.rows, result.obj


def lines_of(obj, name, rows, source) -> dict[int, int]:
    """{diff row: the C line (1-based) the compiled instruction came from}, where the compiler's line table says."""
    from ..source_lines import mismatches
    info = mismatches(obj, name, rows, source)
    return {i['row']: entry['line'] for entry in info.get('lines', []) for i in entry['instructions']}


def _mnemonic(text: str) -> str:
    return (text.split() or [''])[0]


def _operands(text: str) -> str:
    return text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ''


def _replace_line(source: str, index: int, text: str | None) -> str:
    """The source with its line `index` (1-based) replaced by `text`, or removed when it's None."""
    lines = source.split('\n')
    if text is None:
        del lines[index - 1]
    else:
        lines[index - 1] = text
    return '\n'.join(lines)


def _each(line: str, pattern: str, new: str) -> list[str]:
    """The line with one occurrence of `pattern` replaced by `new`, for each occurrence."""
    code = _code_only(line)
    return [line[:m.start()] + new + line[m.end():] for m in re.finditer(pattern, code)]


def edits(source: str, rows, at: dict[int, int], hint) -> list[str]:
    """Candidate sources, each one edit away from `source`, for its differing instructions (diff rows, their lines)."""
    lines = source.split('\n')
    out: list[str] = []

    def add(candidate):
        if candidate != source and candidate not in out:
            out.append(candidate)

    def on_line(index, variants):
        for text in variants:
            add(_replace_line(source, index, text))

    code_lines = [i for i, text in enumerate(lines, 1) if _code_only(text).strip()]
    for row, (marker, target, ours) in enumerate(rows):
        marker = marker.strip()
        tm, om = _mnemonic(target), _mnemonic(ours)
        found = hint(target, ours) if hint and tm and om else None
        if not marker or marker in 'so' or marker == 'r' and found != ('compare',):
            continue  # registers, stack slots, order: the permuter's (but a comparison turned around)
        index = at.get(row)
        # without the compiler's line for it (no line table), every line the edit applies to
        places = [index] if index and index <= len(lines) else code_lines
        if marker == '<' and tm in ('jal', 'jalr'):
            add_call(source, rows, row, at, _operands(target).strip(), add)
            continue
        if marker == '>' and om in ('jal', 'jalr'):
            # a call only this C makes: drop the statement that makes it
            callee = _operands(ours).strip()
            for i in places:
                line = lines[i - 1]
                if callee and re.search(rf'\b{re.escape(callee)}\s*\(', line) and line.strip().endswith(';') \
                        and '{' not in line and '}' not in line:
                    add(_replace_line(source, i, None))
            continue
        for index in places:
            _line_edits(source, lines, index, marker, target, ours, found, add, on_line)
    return out


def _line_edits(source, lines, index, marker, target, ours, found, add, on_line) -> None:
    """The edits one differing instruction asks for on one C line."""
    line = lines[index - 1]
    if marker == 'i':  # the same instruction, other numbers: a constant in the C
        ts, os_ = NUMBER.findall(_operands(target)), NUMBER.findall(_operands(ours))
        if len(ts) == len(os_):
            for t, o in zip(ts, os_):
                if t != o:
                    new, old = int(t, 0), int(o, 0)
                    for m in C_INTEGER.finditer(_code_only(line)):
                        if int(m.group(1), 0) == old and new >= 0:
                            text = (f'0x{new:X}' if m.group(1).lower().startswith('0x') else str(new)) + m.group(2)
                            on_line(index, [line[:m.start()] + text + line[m.end():]])
        return
    if found is None:
        return
    kind = found[0]
    if kind == 'constant':  # `return 0;` where the target returns 1
        old, new = found[1], found[2]
        for m in C_INTEGER.finditer(_code_only(line)):
            if int(m.group(1), 0) == old and new >= 0:
                on_line(index, [line[:m.start()] + str(new) + m.group(2) + line[m.end():]])
    elif kind == 'compare':
        for m in COMPARE.finditer(_code_only(line)):
            for flip in FLIPS[m.group()]:
                on_line(index, [line[:m.start()] + flip + line[m.end():]])
    elif kind == 'operator':
        pattern = OPERATOR.get(found[2])
        if pattern:
            on_line(index, _each(line, pattern, found[1]))
    elif kind in ('signed', 'width'):
        if kind == 'signed':
            retype = SIGN.get
        else:
            size, signed = found[1], found[2]
            wanted = TYPES[(size, signed)] if signed is not None else TYPES[(size, True)]
            retype = (lambda _t, typed=wanted: typed)
            on_line(index, [line[:m.start()] + wanted + line[m.end():] for m in TYPE_WORD.finditer(_code_only(line))])
        if kind == 'signed':
            on_line(index, [line[:m.start()] + SIGN[m.group()] + line[m.end():]
                            for m in re.finditer(r'\b(?:u8|s8|u16|s16|u32|s32)\b', _code_only(line))])
        # the locals, parameters and struct members the line reads or writes
        for name in set(re.findall(r'\b([A-Za-z_]\w*)\b', _code_only(line))):
            retyped = _retype_declaration(source, name, retype)
            if retyped:
                add(retyped)


ADDRESS = re.compile(r'%hi\(([A-Za-z_]\w*)(?:\+(0x[0-9a-fA-F]+|\d+))?\)')
GLOBAL_PLUS = re.compile(r'(?<![\w.>])([A-Za-z_]\w*)\s*\+\s*(0[xX][0-9a-fA-F]+|\d+)(?![\w.])')


def whole_edits(net, proj, header, source, fn, rows) -> list[str]:
    """Edits read from the whole diff, not one line: the structs the C defines laid out again by the offsets that
    differ between the compiled and the target instructions (regional_adapt), numbers that map one way, and
    `name + offset` moved by as much as the address it reaches is off."""
    from . import regional_adapt as ra
    out = []
    pairs = [(t, o) for m, t, o in rows if t and o and m.strip() not in ('<', '>')]
    ours = [scoring.Insn(4 * i, _mnemonic(o), _operands(o), o) for i, (_, o) in enumerate(pairs)]
    theirs = [scoring.Insn(4 * i, _mnemonic(t), _operands(t), t) for i, (t, _) in enumerate(pairs)]
    moves, kept, constants, _ = ra.differences(ours, theirs, proj['arch'])
    if moves or constants:
        laid = ra.adapt_structs(net, proj, header, source, fn, moves, kept, constants)
        for v in (laid, ra.adapt_numbers(laid, moves, constants) if laid else None, ra.adapt_numbers(source, moves, constants)):
            if v and v != source and v not in out:
                out.append(v)
    code = _code_only(source)
    for old, new in moves.items():  # `child[5]` reading offset 0x14 of an array of 4-byte items: index 4 for 0x10
        for width in (4, 2, 1, 8):
            if old % width or new % width or new < 0:
                continue
            for m in re.finditer(rf'\[\s*{old // width}\s*\]', code):
                candidate = source[:m.start()] + f'[{new // width}]' + source[m.end():]
                if candidate not in out:
                    out.append(candidate)
    syms = net.symbols(proj['id'])

    def address(m):
        base = syms.resolve(m.group(1))
        return None if base is None else base + (int(m.group(2), 0) if m.group(2) else 0)
    shifts = {}
    for t, o in pairs:
        mt, mo = ADDRESS.search(t), ADDRESS.search(o)
        if mt and mo:
            want, have = address(mt), address(mo)
            if want is not None and have is not None and want != have:
                shifts.setdefault(have, set()).add(want)
    for have, wants in shifts.items():
        if len(wants) != 1:
            continue
        want = next(iter(wants))
        for m in GLOBAL_PLUS.finditer(code):
            base = syms.resolve(m.group(1))
            number = int(m.group(2), 0)
            if base is not None and base + number == have and want - base >= 0:
                new = want - base
                text = f'0x{new:X}' if m.group(2).lower().startswith('0x') else str(new)
                candidate = source[:m.start(2)] + text + source[m.end(2):]
                if candidate not in out:
                    out.append(candidate)
    return out


def add_call(source, rows, row, at, callee, add) -> None:
    """A call only the target makes: `callee(...)` before the C line of the next instruction this C has, with the
    arguments of a call to it the C makes elsewhere, or none."""
    if not callee or not re.fullmatch(r'[A-Za-z_]\w*', callee):
        return
    after = next((at[r] for r in range(row + 1, len(rows)) if r in at), None)
    before = next((at[r] for r in range(row - 1, -1, -1) if r in at), None)
    lines = source.split('\n')
    calls = [m.group(0) for m in re.finditer(rf'\b{re.escape(callee)}\s*\([^;{{}}]*\)\s*;', _code_only(source))]
    statements = list(dict.fromkeys(calls)) or [f'{callee}();']
    declared = re.search(rf'\b{re.escape(callee)}\s*\(', source) is not None
    for at_line in [x for x in (after, before + 1 if before else None) if x]:
        for statement in statements[:2]:
            indent = re.match(r'\s*', lines[at_line - 1] if at_line <= len(lines) else '').group()
            new = lines[:at_line - 1] + [indent + statement] + lines[at_line - 1:]
            text = '\n'.join(new)
            add(text if declared else f'void {callee}();\n' + text)


def _retype_declaration(source: str, name: str, retype) -> str | None:
    """The source with the declared type of `name` (a local, a parameter or a struct member: `TYPE name;`) changed by
    `retype(old type) -> new type or None`, or None when there's no single such declaration."""
    found = [m for m in re.finditer(rf'\b(u8|s8|u16|s16|u32|s32)(\s+\**\s*){re.escape(name)}\b(?=\s*[;,)\[=])',
                                    _code_only(source))]
    if len(found) != 1:
        return None
    m = found[0]
    new = retype(m.group(1))
    if not new or new == m.group(1):
        return None
    return source[:m.start(1)] + new + source[m.end(1):]


def search(net, proj, header, starts: list[str], fn, *, stop=None) -> tuple[str, int, list[str]] | None:
    """(the closest source found, its score, other sources as close) starting from the closest of `starts`, editing
    where the instructions differ for up to ROUNDS rounds; None when no start builds or the closest is too far off
    for a few edits. The diff can't see everything the judge does (an address this release's listing gives as a
    bare number): sources as close as the best go to the judge too."""
    hint = getattr(processors.get(proj['arch']), 'edit_hint', None)
    best = None
    for source in starts:
        judged = judge(net, proj, header, source, fn)
        if judged and (best is None or judged[0] < best[1]):
            best = (source, judged[0], judged[1], judged[2])
    if best is None or best[1] == 0 or best[1] > MAX_SHARE * max(1, fn['n_insns'] or 1) * 100:
        return (best[0], best[1], []) if best else None
    source, score, rows, obj = best
    ties: list[str] = []
    for _ in range(ROUNDS):
        if stop is not None and stop.is_set():
            break
        at = lines_of(obj, fn['name'], rows, source)
        better = None
        candidates = whole_edits(net, proj, header, source, fn, rows) + edits(source, rows, at, hint)
        for candidate in list(dict.fromkeys(candidates))[:PER_ROUND]:
            judged = judge(net, proj, header, candidate, fn)
            if judged and judged[0] < (better[1] if better else score):
                better = (candidate, *judged)
                if judged[0] == 0:
                    break
            elif judged and judged[0] == score and len(ties) < MAX_TIES:
                ties.append(candidate)
        if better is None:
            break
        source, score, rows, obj = better
        ties = []
        if score == 0:
            break
    return source, score, ties
