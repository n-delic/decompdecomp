"""Separate target edits from declarations and neighbouring functions without asserting causality."""
import difflib

from .forks.csource import find


def focused(before, after, name):
    a, b = find(before, name), find(after, name)
    if not a or not b:
        return {'function_diff': None, 'supporting_diff': None, 'focus_note': 'Target could not be isolated; full source diff is retained.'}
    diff = lambda x, y, label: ''.join(difflib.unified_diff(x.splitlines(True), y.splitlines(True),
                                                          fromfile='before '+label, tofile='matched '+label))
    result = {'function_diff': diff(before[a[0]:a[1]], after[b[0]:b[1]], name),
            'supporting_diff': diff(before[:a[0]]+before[a[1]:], after[:b[0]]+after[b[1]:], 'supporting source'),
            'focus_note': 'Declarations and neighbouring functions may affect compilation; neither diff alone proves the cause.'}

    from .app.captured_lifetimes import variants as lifetime_variants
    from .app.observed_repairs import variants
    from .repair_changes import TOKEN, without_comments
    tokens = lambda text: TOKEN.findall(without_comments(text))
    target = tokens(after[b[0]:b[1]])
    matches = []
    if len(before) < 64000:
        for family, candidate in variants(before, name) + lifetime_variants(before, name):
            span = find(candidate, name)
            if span and tokens(candidate[span[0]:span[1]]) == target:
                matches.append(family)
    result['pattern_candidates'] = sorted(set(matches))
    result['pattern_note'] = 'Pattern candidates reproduce the target-function edit only; supporting edits may also matter. Replay before promotion.'
    return result
