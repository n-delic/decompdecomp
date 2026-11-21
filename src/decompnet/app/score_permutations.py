"""score_permutations: an agent scores many small reorderings or edit combinations at once with the exact judge.

A person finishing a near-match grids 3-4 statements' orders, or every combination of a few individually neutral
edits, through `decomp judge-worker` (the app's own judge, standalone, about 0.1 s a judgment): one switch case went
610 -> 420 -> 240 -> 0 that way where reordering by eye stalled. Helpers could only send six variants at a time,
each counting toward their move-on number. This judges up to MAX candidates in a small judge_pool (the judge the
app uses, attach-only, recording nothing), then sends only the best through the ordinary compile path, and only
when it beats the starting score: that one is judged again, recorded and saved as usual, so a match is still
byte-identical, and a batch that finds nothing records nothing and costs no move-on count.

Project mode (a public decomp's own build, judged by objdiff per unit) has no standalone judge worker yet, so the
tool says so there (ROADMAP.md)."""
import itertools
import time

from ..network import NetworkError
from ..project import compiler_for
from . import agent_targets, agent_tokens

MAX = 64
WORKERS = 2  # beside the app's own compiles and any permuter: a batch is short, it needs no more
SECONDS = 90


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


def run(net, agent, slug, function, revision, statements=None, patches=None, max=MAX):
    fn = net._function(function, slug)
    if net.fork_slug(slug):
        raise NetworkError('score_permutations works in ROM projects only for now: this project is judged by its own '
                           'build. Use compile_variants(combine=true) instead.')
    source, actual = agent_tokens.candidate(net, agent, slug, function)
    if actual != revision:
        raise agent_tokens.stale(net, agent, fn, revision, source, actual)
    limit = min(MAX, int(max or MAX))
    sources = candidates(source, statements, patches, limit)
    if not sources:
        raise NetworkError('No candidate differs from the current revision.')
    proj = net._project(slug)
    own = compiler_for(net.cfg(proj), fn['vram'], fn)
    flags = own['flags'].split() if isinstance(own.get('flags'), str) else own.get('flags') or []
    from . import judge_pool
    deadline = time.monotonic() + SECONDS

    def check():
        if time.monotonic() > deadline:
            raise TimeoutError

    scores = []
    with judge_pool.JudgePool(slug, fn, own['toolchain'], flags, size=WORKERS) as pool:
        try:
            scores = pool.judge_many(sources, check=check, chunk=WORKERS * 4)
        except TimeoutError:
            pass
        errors = list(pool.errors)
    return report(net, agent, slug, function, fn, source, sources, scores, errors, limit)


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
    from .tools import _compile
    lines.append(f'Best {best} (candidate {index + 1}); judged again and recorded through compile_and_diff:')
    lines.append(_compile(net, agent, slug, function, sources[index]))
    return '\n'.join(lines)
