"""Order a generated batch so candidates that edit the C lines behind the asm difference are compiled first.

The compiler's own line table says which C line each differing instruction came from (source_lines.py, through a -g
twin that is used only when its code is byte-identical). A line association under optimization is advice, not proof,
so nothing is dropped: candidates that touch no marked line keep their place after the ones that do, and a parent
with no usable line map is left in the order it came.
"""
"""What the compiler's line table lets the deep permuter do: keep rewrites on the C lines behind the asm difference.

The compiler says which C line each differing instruction came from (source_lines.py, through a -g twin that is used
only when its code is byte-identical). A line association under optimization is advice, not proof, so it only orders
and thins work (nothing is judged here): a parent with no usable line map is left as it came. What is built on it:

- ranking and thinning of each generated batch by the lines a candidate edits (`rank`, `thin`), by tokens, because the
  permuter reprints a function with its own layout and a line diff would call every line changed;
- the marked lines sent to the generator (`order.focus`), widened to the definitions of the variables used on them
  when a parent has stalled (`widen`: the line the compiler blames is often the one that consumes the wrong value);
- crossover of two candidates whose edits are on different lines (`merge`);
- what was learnt from measured trials: whether marked-line candidates really improve more (`outcome`, `summary`),
  and rewrites on a line that never changed the compiled code (`dead`);
- the statement's shape (`shape`), a second key for the adaptive repair policy.
"""
import difflib
import hashlib
import re
from collections import Counter, OrderedDict

from .. import compile_timing, scoring

REMEMBERED = 8
DIFFERENT, OPERAND = 2, 1  # a moved or extra instruction points at its line harder than a differing operand
STALL_ROUNDS = 3  # generator jobs from one parent without an improvement before the focus follows definitions back
MAX_HOPS = 2
DEAD_AFTER = 3  # compiles of one rewrite on one set of lines that left the code as it was, before it is skipped
MAX_MERGE_STATEMENTS = 40  # edits of more statements than this are a rewrite of the function, not an edit

_TOKEN = re.compile(r'''(?P<skip>\s+|/\*.*?\*/|//[^\n]*)|(?P<tok>^[ \t]*\#[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|[A-Za-z_]\w*
    |0[xX][0-9a-fA-F]+[uUlL]*|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?[fFuUlL]*|->|<<=|>>=|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|.)''',
                    re.S | re.M | re.X)


def tokens(text: str) -> list[tuple[str, int]]:
    """[(token, 1-based line)] without comments and layout: two layouts of the same code have the same tokens."""
    out, line, at = [], 1, 0
    for match in _TOKEN.finditer(text):
        line += text.count('\n', at, match.start())
        at = match.start()
        if match.lastgroup == 'tok':
            out.append((match.group().strip(), line))
    return out


_cache: OrderedDict[str, list] = OrderedDict()


def units(text: str) -> list[tuple[tuple[str, ...], frozenset[int]]]:
    """The statements of `text` as ((tokens), lines they are on): a unit ends at `;`, `{` or `}` (or is a directive).
    Diffing statements, not tokens, is hundreds of times faster on a long function and is the granularity that matters."""
    if text not in _cache:
        found, current, lines = [], [], set()
        for token, line in tokens(text):
            current.append(token)
            lines.add(line)
            if token in (';', '{', '}') or token.startswith('#'):
                found.append((tuple(current), frozenset(lines)))
                current, lines = [], set()
        if current:
            found.append((tuple(current), frozenset(lines)))
        _cache[text] = found
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return _cache[text]


def _edits(parent: str, candidate: str) -> list[tuple[int, int, list]]:
    """[(first parent statement, one past the last, the candidate's statements there)] for each place they differ."""
    before, after = [u for u, _ in units(parent)], units(candidate)
    return [(i1, i2, after[j1:j2]) for tag, i1, i2, j1, j2
            in difflib.SequenceMatcher(None, before, [u for u, _ in after], autojunk=False).get_opcodes() if tag != 'equal']


def _lines_of(parent: str, edits) -> set[int]:
    marks = units(parent)
    lines: set[int] = set()
    for i1, i2, _ in edits:
        if i2 > i1:
            for i in range(i1, min(i2, len(marks))):
                lines |= marks[i][1]
        else:  # an insertion touches the statements either side of it
            for i in (i1 - 1, i1):
                if 0 <= i < len(marks):
                    lines |= marks[i][1]
    return lines


def touched_lines(parent: str, candidate: str) -> set[int]:
    """The 1-based lines of `parent` whose statements `candidate` rewrites; an insertion touches the lines around it."""
    return _lines_of(parent, _edits(parent, candidate))


def _render(parts: list[str]) -> str:
    """C from tokens: one space between tokens, a new line after ; { } and a directive on a line of its own."""
    out = []
    for token in parts:
        out.append(token)
        out.append('\n' if token in (';', '{', '}') or token.startswith('#') else ' ')
    return ''.join(out).rstrip() + '\n'


def merge(base: str, first: str, second: str) -> str | None:
    """`base` with the edits of both candidates, when they are on different lines and neither rewrites the function
    wholesale; else None. Edits that depend on each other just fail to compile or judge worse, as any rewrite can."""
    one, two = _edits(base, first), _edits(base, second)
    if not one or not two or sum(len(e[2]) + e[1] - e[0] for e in one + two) > MAX_MERGE_STATEMENTS:
        return None
    if _lines_of(base, one) & _lines_of(base, two):
        return None
    parts = [list(u) for u, _ in units(base)]
    for i1, i2, new in sorted(one + two, key=lambda e: -e[0]):
        parts[i1:i2] = [list(u) for u, _ in new]
    merged = _render([token for unit in parts for token in unit])
    return None if merged in (first, second) else merged


def crossovers(base: str, parents: list[str], limit: int = 3, tried=None) -> list[str]:
    """Up to `limit` merges of pairs of `parents` (frontier candidates) made against `base`, none tried before."""
    found, tried = [], tried if tried is not None else set()
    for index, first in enumerate(parents):
        for second in parents[index + 1:]:
            if first == base or second == base or first == second:
                continue
            key = hashlib.sha1((first + '\0' + second).encode()).hexdigest()
            if key in tried:
                continue
            tried.add(key)
            merged = merge(base, first, second)
            if merged and merged not in found:
                found.append(merged)
                if len(found) == limit:
                    return found
    return found


_IDENT = re.compile(r'[A-Za-z_]\w*')
_KEYWORDS = frozenset('if else while for do switch case default return break continue goto sizeof typedef struct union '
                      'enum static extern const volatile register unsigned signed void int char short long float double'.split())


class Prefixed:
    """`first`, then the rest of `batch`, closing it as a generator is closed."""

    def __init__(self, first, batch):
        self.first, self.batch = first, batch

    def __iter__(self):
        yield from self.first
        yield from self.batch

    def close(self):
        if hasattr(self.batch, 'close'):
            self.batch.close()


def widen(source: str, focus: dict[int, int], hops: int = 1) -> dict[int, int]:
    """`focus` plus, for each hop, the nearest earlier line that sets each variable a marked line reads."""
    lines = source.split('\n')
    wide, frontier = dict(focus), set(focus)
    for _ in range(hops):
        found: set[int] = set()
        for number in frontier:
            if not 1 <= number <= len(lines):
                continue
            text = ' '.join(token for token, _ in tokens(lines[number - 1]))
            for name in set(_IDENT.findall(text)) - _KEYWORDS:
                setter = re.compile(rf'(?<![\w.>])(?:[A-Za-z_]\w*[\s*]+)?{re.escape(name)}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|(?:\+\+|--)\s*{re.escape(name)}\b|\b{re.escape(name)}\s*(?:\+\+|--)')
                for earlier in range(number - 1, 0, -1):
                    if setter.search(lines[earlier - 1]):
                        found.add(earlier)
                        break
        frontier = {line for line in found if line not in wide}
        wide.update({line: OPERAND for line in frontier})
    return wide


_SHAPES = (('call', r'\b(?!(?:if|while|for|switch|sizeof|return)\b)[A-Za-z_]\w*\s*\('), ('cond', r'\b(?:if|while|for|switch)\b|\?'),
           ('mul', r'[\w)\]]\s*\*\s*(?:\d|0[xX])'), ('div', r'[\w)\]]\s*[/%]'), ('shift', r'<<|>>'), ('logic', r'&&|\|\|'),
           ('bits', r'[^&|]([&|^])[^&|=]'), ('index', r'\['), ('field', r'->|\w\.\w'),
           ('cast', r'\(\s*(?:unsigned|signed|[su](?:8|16|32|64)|int|char|short|long|float|double)\b[^)]*\)'),
           ('float', r'\d\.\d|\bf32\b|\bfloat\b|\bdouble\b'), ('store', r'(?:\*|->|\])[^=;]*[^=!<>]=[^=]'))


def shape(source: str, lines) -> str:
    """A few words for what the statements on `lines` are made of (`call+field`), the key the adaptive policy learns
    by besides the mismatch kind. 'plain' when nothing special is on them; '' when there are no lines."""
    rows = source.split('\n')
    text = ' '.join(' '.join(t for t, _ in tokens(rows[n - 1])) for n in sorted(lines) if 1 <= n <= len(rows))
    if not text.strip():
        return ''
    tags = [name for name, pattern in _SHAPES if re.search(pattern, text)]
    return '+'.join(tags[:3]) or 'plain'


def _touched(parent: str, candidates: list) -> list[set[int]]:
    before = [u for u, _ in units(parent)]
    return [_lines_of(parent, _edits(parent, candidate)) if before else set() for candidate in candidates]


def rank(parent: str, candidates: list, focus: dict[int, int], touched=None) -> list:
    """`candidates`, those touching more weighted focus lines first, the rest in their given order."""
    if not focus or len(candidates) < 2:
        return candidates
    touched = touched if touched is not None else _touched(parent, candidates)
    scores = [sum(focus.get(line, 0) for line in lines) for lines in touched]
    order = sorted(range(len(candidates)), key=lambda index: -scores[index])
    return [candidates[index] for index in order]


EXPLORE = 4  # of the candidates that touch no marked line, one in this many is still compiled


def thin(parent: str, candidates: list, focus: dict[int, int]) -> list:
    """`rank`, then only the candidates that touch a marked line plus a quarter of the rest, so the batch written
    wider than the compile budget (permute.LINE_FOCUS_OVERGENERATION) costs about what a plain one does. A batch
    that is too small to thin, or touches nothing marked, is only ranked."""
    if not focus or len(candidates) < 2:
        return candidates
    touched = _touched(parent, candidates)
    ranked = rank(parent, candidates, focus, touched)
    if len(ranked) < 8:
        return ranked
    hits = sum(1 for lines in touched if lines & focus.keys())
    if not hits:
        return ranked
    return ranked[:hits + max(1, (len(ranked) - hits) // EXPLORE)]


# What happened to the candidates that were ranked, so the next batches (and the user) can tell whether the focus pays.
_seen: OrderedDict[str, tuple] = OrderedDict()  # candidate -> (parent, lines it edits, on a marked line?, rewrite)
_parent_output: dict[str, str | None] = {}
_dead: Counter = Counter()  # (parent, lines, rewrite) -> compiles that left the code as it was
_alive: set = set()  # (parent, lines, rewrite) that changed it, or improved it
stats: Counter = Counter()


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def register(parent: str, candidates: list, focus: dict[int, int]) -> None:
    """Remember what each candidate of a batch edits, for `outcome`."""
    parent_key = _key(parent)
    for candidate in candidates:
        touched = frozenset(touched_lines(parent, candidate))
        hit = bool(touched & focus.keys())
        lineage = getattr(candidate, 'lineage', None) or ()
        _seen[_key(candidate)] = (parent_key, touched, hit, lineage[-1] if lineage else '')
        stats['written_marked' if hit else 'written_other'] += 1
    while len(_seen) > 4096:
        _seen.popitem(last=False)


def dead(parent: str, candidate) -> bool:
    """True for a rewrite that has `DEAD_AFTER` times, on these same lines of this parent, compiled to the code the
    parent compiles to without ever improving it: nothing it does there reaches the object."""
    if not _dead:
        return False
    lineage = getattr(candidate, 'lineage', None) or ()
    key = (_key(parent), frozenset(touched_lines(parent, candidate)), lineage[-1] if lineage else '')
    return _dead[key] >= DEAD_AFTER and key not in _alive


def outcome(candidate: str, improved: bool, output: str | None) -> None:
    """A ranked candidate's result: did it improve the score, and did its compiled code differ from its parent's?"""
    seen = _seen.pop(_key(candidate), None)
    if seen is None:
        return
    parent_key, touched, hit, rewrite = seen
    name = 'marked' if hit else 'other'
    stats[f'compiled_{name}'] += 1
    stats[f'improved_{name}'] += int(improved)
    compile_timing.add(f'line_focus_compiled_{name}', 1)
    compile_timing.add(f'line_focus_improved_{name}', int(improved))
    key = (parent_key, touched, rewrite)
    if improved or (output is not None and output != _parent_output.get(parent_key, output)):
        _alive.add(key)
    elif output is not None and output == _parent_output.get(parent_key):
        _dead[key] += 1
        stats['no_effect'] += 1


def summary() -> str:
    """One line on whether candidates on the marked lines improve more often than the rest ('' before any compile)."""
    marked, other = stats['compiled_marked'], stats['compiled_other']
    if marked + other < 40:
        return ''
    rate = lambda won, total: f'{100 * won / total:.1f}%' if total else 'none compiled'
    return (f"C line focus: {stats['improved_marked']} of {marked} candidates on marked lines improved ({rate(stats['improved_marked'], marked)}), "
            f"{stats['improved_other']} of {other} elsewhere ({rate(stats['improved_other'], other)}); "
            f"{stats['no_effect']} compiled to the parent's code.")


def line_info(net, proj, fn, source: str) -> dict | None:
    """source_lines.mismatches for this source's own compile (through the -g twin when it has no line table), or None
    when it can't be judged here (a project without the ROM judge, a compile error)."""
    from ..source_lines import mismatches
    context = net._context(proj['id'], fn['unit'])  # (a database row, not a dict: no .get)
    header = context['header'] if context else ''
    result = net._compile(proj, header, source, fn)
    if not result.ok:
        return None
    cand, verdict, _ = net._evaluate(fn, proj, result.obj)
    if cand is None:
        return None
    diff = scoring.diff(net._target_obj(fn, proj), cand)
    obj = net.line_object(proj, header, source, fn, result) or result.obj
    from .experiment_evidence import output
    info = mismatches(obj, fn['name'], diff.rows, source, 'func.cpp' if result.language == 'c++' else 'func.c', arch=proj['arch'])
    info['output'] = output(cand)  # (what the parent compiles to: `outcome` compares the candidates' with it)
    return info


def measure(net, proj, fn, source: str) -> tuple[dict[int, int], str | None]:
    """({C line: weight}, compiled-code fingerprint) for this source: the lines the compiler ties to its differing
    instructions; ({}, None) when it can't say."""
    info = line_info(net, proj, fn, source)
    if not info:
        return {}, None
    return ({entry['line']: DIFFERENT if entry['severity'] == 'different' else OPERAND for entry in info['lines']},
            info.get('output'))


def focus_lines(net, proj, fn, source: str) -> dict[int, int]:
    """{C line: weight} for the lines the compiler ties to this source's differing instructions; {} when it can't."""
    return measure(net, proj, fn, source)[0]


def locator(net, proj, fn, mapped):
    """`located(source)` for a local-repair search: the repair locations its trials recorded, else, when those have no
    line map (a GCC game built without -g), the map of that source's own compile and its byte-identical -g twin, made
    once per source. The localised repair families (targeted storage, scan loads, loop shapes, layout) read it."""
    from decomprepair import repair_localisation
    can = all(hasattr(net, name) for name in ('_evaluate', '_target_obj', 'line_object', '_compile'))
    made: dict[str, dict | None] = {}

    def located(source):
        recorded = mapped.get(source)
        if recorded and (recorded.get('mapping') or {}).get('available') or not can:
            return recorded
        if source not in made:
            try:
                info = line_info(net, proj, fn, source)
            except Exception:  # advice only: a broken map must never stop a search
                info = None
            made[source] = repair_localisation.bind(source, info) if info and info.get('available') else recorded
        return made[source]

    return located


def for_function(net, proj, fn, log, then=None, finder=measure):
    """The batch-ordering function for a function's search (None: a project that can't judge this way, as fork mode),
    followed by `then`, another such function, which orders what the lines leave tied. `.focus(parent)` is the
    marked lines for a generator job, `.shape(parent)` their shape, `.record(...)` and `.crossovers(...)` the rest."""
    if not all(hasattr(net, name) for name in ('_evaluate', '_target_obj', 'line_object', '_compile')):
        return then
    known: OrderedDict[str, dict[int, int]] = OrderedDict()
    rounds: Counter = Counter()  # generator jobs made from each parent
    reported, widened = set(), set()

    def measured(parent):
        key = _key(parent)
        if key not in known:
            try:
                found = finder(net, proj, fn, parent)
            except Exception as error:  # advice only: a broken map must never stop a search
                log(f'C line focus skipped: {error}')
                found = {}
            focus, output = found if isinstance(found, tuple) else (found, None)
            known[key] = focus
            _parent_output[key] = output
            while len(known) > REMEMBERED:
                known.popitem(last=False)
            if focus and key not in reported:
                reported.add(key)
                log('C line focus: differing instructions come from line' + ('s ' if len(focus) > 1 else ' ')
                    + ', '.join(map(str, sorted(focus))) + '; rewrites are kept on them and candidates editing them are compiled first.')
        return key, known[key]

    def focus_of(parent):
        """{line: weight}: the marked lines, and once the parent has stalled the definitions they read."""
        key, focus = measured(parent)
        hops = min(MAX_HOPS, rounds[key] // STALL_ROUNDS)
        if focus and hops:
            if (key, hops) not in widened:
                widened.add((key, hops))
                log(f'C line focus: no improvement after {rounds[key]} batches from this parent; following the variables '
                    f'on the marked lines back {hops} definition{"s" if hops > 1 else ""}.')
            return widen(parent, focus, hops)
        return focus

    def order(parent, candidates):
        if then is not None:
            candidates = then(parent, candidates)
        if parent is None or len(candidates) < 2:
            return candidates
        focus = focus_of(parent)
        candidates = [c for c in candidates if not dead(parent, c)] or list(candidates)
        if focus:
            register(parent, candidates, focus)
        return thin(parent, list(candidates), focus)

    def lines(parent):
        """The parent's marked lines for a generator job (empty when unknown); each call counts as one batch from it."""
        if not parent:
            return []
        rounds[_key(parent)] += 1
        return sorted(focus_of(parent))

    def shape_of(parent):
        return shape(parent, sorted(measured(parent)[1])) if parent else ''

    order.focus, order.shape, order.crossovers = lines, shape_of, crossovers
    return order
