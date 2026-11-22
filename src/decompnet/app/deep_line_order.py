"""Order a generated batch so candidates that edit the C lines behind the asm difference are compiled first.

The compiler's own line table says which C line each differing instruction came from (source_lines.py, through a -g
twin that is used only when its code is byte-identical). A line association under optimization is advice, not proof,
so nothing is dropped: candidates that touch no marked line keep their place after the ones that do, and a parent
with no usable line map is left in the order it came.
"""
import difflib
import hashlib
from collections import OrderedDict

from .. import scoring

REMEMBERED = 8
DIFFERENT, OPERAND = 2, 1  # a moved or extra instruction points at its line harder than a differing operand


def touched_lines(parent: str, candidate: str) -> set[int]:
    """The 1-based lines of `parent` that `candidate` rewrites; an insertion touches the two lines around it."""
    before, after = parent.split('\n'), candidate.split('\n')
    lines: set[int] = set()
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        lines.update(range(i1 + 1, i2 + 1) if i2 > i1 else (i1, i1 + 1))
    return lines


def rank(parent: str, candidates: list, focus: dict[int, int]) -> list:
    """`candidates`, those touching more weighted focus lines first, the rest in their given order."""
    if not focus or len(candidates) < 2:
        return candidates
    scores = [sum(focus.get(line, 0) for line in touched_lines(parent, candidate)) for candidate in candidates]
    order = sorted(range(len(candidates)), key=lambda index: -scores[index])
    return [candidates[index] for index in order]


def focus_lines(net, proj, fn, source: str) -> dict[int, int]:
    """{C line: weight} for the lines the compiler ties to this source's differing instructions; {} when it can't."""
    from ..source_lines import mismatches
    header = (net._context(proj['id'], fn['unit']) or {}).get('header', '')
    result = net._compile(proj, header, source, fn)
    if not result.ok:
        return {}
    cand, verdict, _ = net._evaluate(fn, proj, result.obj)
    if cand is None:
        return {}
    diff = scoring.diff(net._target_obj(fn, proj), cand)
    obj = net.line_object(proj, header, source, fn, result) or result.obj
    info = mismatches(obj, fn['name'], diff.rows, source, 'func.cpp' if result.language == 'c++' else 'func.c')
    return {entry['line']: DIFFERENT if entry['severity'] == 'different' else OPERAND for entry in info['lines']}


def for_function(net, proj, fn, log, then=None, finder=focus_lines):
    """The batch-ordering function for a function's search (None: a project that can't judge this way, as fork mode),
    followed by `then`, another such function, which orders what the lines leave tied."""
    if not all(hasattr(net, name) for name in ('_evaluate', '_target_obj', 'line_object', '_compile')):
        return then
    known: OrderedDict[str, dict[int, int]] = OrderedDict()
    reported = set()

    def order(parent, candidates):
        if then is not None:
            candidates = then(parent, candidates)
        if parent is None or len(candidates) < 2:
            return candidates
        key = hashlib.sha1(parent.encode()).hexdigest()
        if key not in known:
            try:
                known[key] = finder(net, proj, fn, parent)
            except Exception as error:  # advice only: a broken map must never stop a search
                log(f'C line focus skipped: {error}')
                known[key] = {}
            while len(known) > REMEMBERED:
                known.popitem(last=False)
        focus = known[key]
        ranked = rank(parent, list(candidates), focus)
        if focus and key not in reported:
            reported.add(key)
            log('C line focus: differing instructions come from line' + ('s ' if len(focus) > 1 else ' ')
                + ', '.join(map(str, sorted(focus))) + '; candidates editing them are compiled first.')
        return ranked

    return order
