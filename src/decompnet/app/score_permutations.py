"""score_permutations: an agent scores many small reorderings or edit combinations at once with the exact judge.

A person finishing a near-match grids 3-4 statements' orders, or every combination of a few individually neutral
edits, through `decomp judge-worker` (the app's own judge, standalone, about 0.1 s a judgment): one switch case went
610 -> 420 -> 240 -> 0 that way where reordering by eye stalled. Helpers could only send six variants at a time,
each counting toward their move-on number. This judges up to MAX candidates in a small judge_pool (the judge the
app uses, attach-only, recording nothing), then sends only the best through the ordinary compile path, and only
when it beats the starting score: that one is judged again, recorded and saved as usual, so a match is still
byte-identical, and a batch that finds nothing records nothing and costs no move-on count.

Project mode (a public decomp's own build, judged by objdiff per unit) has no standalone judge worker yet, so the
tool says so there (ROADMAP.md).

A larger grid takes a marked source, the markers of the skill's grid.py: `/*{*/a/*|*/b/*}*/` for alternatives and
`/*permute{*/ ... /*}permute*/` around one statement per line. Every combination is a candidate, up to MAX_GRID (a hand
session's grid of a few thousand found the last rows where six variants at a time could not); it runs on the same
two workers under a longer deadline, so it stays bounded in CPU and time."""
import itertools
import math
import re
import time
from collections import Counter

from ..network import NetworkError
from ..project import compiler_for
from . import agent_targets, agent_tokens

MAX = 64
MAX_GRID = 2000
WORKERS = 2  # beside the app's own compiles and any permuter: a batch is short, it needs no more
SECONDS = 90
GRID_SECONDS = 300
ALT = re.compile(r"/\*\{\*/(.*?)/\*\}\*/", re.S)
SEPARATOR = "/*|*/"
BLOCK = re.compile(r"/\*permute\{\*/(.*?)/\*\}permute\*/", re.S)
PLACEHOLDER = "\x00A{}\x00"
PLACEHOLDERS = re.compile(r"\x00A(\d+)\x00")


def _unique_orders(lines):
    """Every order of the lines as index tuples, equal lines taken in written order (none twice)."""
    free = {}
    for i, text in enumerate(lines):
        free.setdefault(text, []).append(i)

    def walk(prefix):
        if len(prefix) == len(lines):
            yield tuple(prefix)
            return
        for indexes in free.values():
            if indexes:
                i = indexes.pop(0)
                prefix.append(i)
                yield from walk(prefix)
                prefix.pop()
                indexes.insert(0, i)
    return walk([])


def _distinct_orders(lines):
    count = math.factorial(len(lines))
    for repeats in Counter(lines).values():
        count //= math.factorial(repeats)
    return count


def grid(marked, limit=MAX_GRID):
    """[(description, source)] for every combination of the marked choices (grid.py's markers), refusing more than
    `limit` with which choice made it so many."""
    alternatives = []

    def take(match):
        options = match[1].split(SEPARATOR)
        if any("/*{*/" in o or "/*permute{*/" in o for o in options):
            raise NetworkError("Alternatives don't nest: an alternative holds another /*{*/ or /*permute{*/ marker.")
        alternatives.append(options)
        return PLACEHOLDER.format(len(alternatives) - 1)

    text = ALT.sub(take, marked)
    if "/*{*/" in text or "/*}*/" in text:
        raise NetworkError("A /*{*/ marker has no matching /*}*/.")
    if text.count("/*permute{*/") != len(BLOCK.findall(text)) or "/*}permute*/" in BLOCK.sub("", text):
        raise NetworkError("A /*permute{*/ marker has no matching /*}permute*/.")
    parts, blocks, last = [], [], 0
    for match in BLOCK.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        indent = re.match(r"[ \t]*", text[line_start:match.start()])[0]
        lines = [line.strip() for line in match[1].split("\n") if line.strip()]
        if len(lines) < 2:
            raise NetworkError("A permute block needs at least two statements, one per line.")
        parts.append(text[last:match.start()])
        blocks.append((indent, lines))
        last = match.end()
    parts.append(text[last:])
    sizes = [len(o) for o in alternatives if len(o) > 1] + [_distinct_orders(lines) for _, lines in blocks]
    total = math.prod(sizes) if sizes else 1
    if total < 2:
        raise NetworkError("Mark at least one choice: /*{*/a/*|*/b/*}*/ or a /*permute{*/ block of 2+ lines.")
    if total > limit:
        names = [f"{len(o)} alternatives" for o in alternatives if len(o) > 1]
        names += [f"a permute block of {len(lines)} lines = {_distinct_orders(lines)} orders" for _, lines in blocks]
        raise NetworkError(f"{total} candidates is more than {limit} ({' x '.join(names)}): mark fewer choices.")
    axes = [("alt", i, range(len(o))) for i, o in enumerate(alternatives) if len(o) > 1]
    axes += [("permute", b, list(_unique_orders(lines))) for b, (_, lines) in enumerate(blocks)]
    out = []
    for combo in itertools.product(*(values for _, _, values in axes)):
        picks = [0] * len(alternatives)
        orders = [tuple(range(len(lines))) for _, lines in blocks]
        words = []
        for (kind, which, _), value in zip(axes, combo):
            if kind == "alt":
                picks[which] = value
                words.append(f"alt{which + 1}={value + 1}")
            else:
                orders[which] = value
                words.append(f"block{which + 1}=" + ",".join(str(i + 1) for i in value))
        body = [parts[0]]
        for b, (indent, lines) in enumerate(blocks):
            body.append(("\n" + indent).join(lines[i] for i in orders[b]))
            body.append(parts[b + 1])
        out.append((" ".join(words), PLACEHOLDERS.sub(lambda m, picks=picks: alternatives[int(m[1])][picks[int(m[1])]], "".join(body))))
    return out


def candidates(source, statements=None, patches=None, limit=MAX):
    """The sources to judge: every order of `statements` (exact lines of the source, kept in the slots they
    occupy), or every non-empty combination of `patches` (independent old/new edits). The source itself is left out."""
    if bool(statements) == bool(patches):
        raise NetworkError('Send either statements (2-6 exact lines to reorder) or patches (2-6 old/new edits to combine).')
    out = []
    if statements:
        if not 2 <= len(statements) <= 6:
            raise NetworkError('Send 2-6 statements to reorder.')
        lines = source.split('\n')
        slots = []
        for text in statements:
            text = str(text).strip()
            found = [i for i, line in enumerate(lines) if line.strip() == text and i not in slots]
            if not found:
                raise NetworkError(f'Statement not found as a whole line of the candidate: {text[:80]!r}.')
            slots.append(found[0])
        order = sorted(slots)
        for perm in itertools.permutations(slots):
            if list(perm) == order:
                continue
            new = list(lines)
            for slot, origin in zip(order, perm):
                new[slot] = lines[origin]
            out.append('\n'.join(new))
            if len(out) >= limit:
                break
        return out
    if not 2 <= len(patches) <= 6:
        raise NetworkError('Send 2-6 patches to combine.')
    for size in range(1, len(patches) + 1):
        for subset in itertools.combinations(patches, size):
            text = source
            try:
                for p in subset:
                    text = agent_targets.replace_anchor(text, p.get('old'), p.get('new', ''), p.get('occurrence', 0))
            except (NetworkError, AttributeError):
                continue  # overlapping edits: this combination doesn't exist
            if text != source and text not in out:
                out.append(text)
            if len(out) >= limit:
                return out
    return out


def run(net, agent, slug, function, revision, statements=None, patches=None, max=MAX, marked=''):
    fn = net._function(function, slug)
    if net.fork_slug(slug):
        raise NetworkError('score_permutations works in ROM projects only for now: this project is judged by its own '
                           'build. Use compile_variants(combine=true) instead.')
    source, actual = agent_tokens.candidate(net, agent, slug, function)
    if actual != revision:
        raise agent_tokens.stale(net, agent, fn, revision, source, actual)
    if marked:
        if statements or patches:
            raise NetworkError('Send a marked source, or statements, or patches: one kind at a time.')
        limit = min(MAX_GRID, int(max or MAX_GRID)) if int(max or 0) > MAX else MAX_GRID
        sources = [text for _, text in grid(marked, limit) if text != source]
        seconds = GRID_SECONDS
    else:
        limit = min(MAX, int(max or MAX))
        sources = candidates(source, statements, patches, limit)
        seconds = SECONDS
    if not sources:
        raise NetworkError('No candidate differs from the current revision.')
    proj = net._project(slug)
    own = compiler_for(net.cfg(proj), fn['vram'], fn)
    flags = own['flags'].split() if isinstance(own.get('flags'), str) else own.get('flags') or []
    from . import judge_pool
    deadline = time.monotonic() + seconds

    def check():
        if time.monotonic() > deadline:
            raise TimeoutError

    with judge_pool.JudgePool(slug, fn, own['toolchain'], flags, size=WORKERS) as pool:
        scores = judge_until(pool, sources, check, WORKERS * 4)
        errors = list(pool.errors)
    return report(net, agent, slug, function, fn, source, sources, scores, errors, limit)


def judge_until(pool, sources, check, chunk):
    """Scores of `sources` in order, chunk by chunk, keeping what was judged when the deadline passes (a 2000-candidate
    grid that runs out of time still reports, and records, its best so far)."""
    scores = []
    for at in range(0, len(sources), chunk):
        try:
            check()
        except TimeoutError:
            break
        scores += pool.judge_many(sources[at:at + chunk], chunk=chunk)
    return scores


def report(net, agent, slug, function, fn, source, sources, scores, errors, limit):
    judged = [(s, i) for i, s in enumerate(scores) if s is not None]
    head = f'Judged {len(scores)} of {len(sources)} candidates (at most {limit}) with the exact judge; nothing recorded yet.'
    if not judged:
        return head + ' None compiled.' + (f' First error: {errors[0][:300]}' if errors else '')
    histogram = {}
    for s, _ in judged:
        histogram[s] = histogram.get(s, 0) + 1
    best, index = min(judged)
    start = net._function(function, slug)['best_score']
    lines = [head, 'Scores (score: candidates): ' + ', '.join(f'{s}: {n}' for s, n in sorted(histogram.items())[:12])]
    if start is not None and best >= start:
        lines.append(f'Best {best} does not beat the saved best {start}: nothing saved, no move-on count spent. '
                     'Combine these with other neutral edits, or read guide("near-match").')
        return '\n'.join(lines)
    lines.append(f'Best {best} (candidate {index + 1}); judged again and recorded through compile_and_diff:')
    from .tools import _compile
    lines.append(_compile(net, agent, slug, function, sources[index]))
    return '\n'.join(lines)
