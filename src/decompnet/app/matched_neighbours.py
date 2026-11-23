"""The matched functions closest to one function, with a snippet of their matching C (the brief, the skill's orient).

An agent starting on a function reads the closest matched code first when it knows where it is: a caller that shows
how the result is used, a callee whose prototype it needs, the function before and after it (one source file,
often one author's habits: the same temporaries, the same loop shapes), one that reads the same globals, one whose
instructions look alike. The brief listed callers and callees by name and look-alikes by score, but never the matched
C of the others, so agents searched for them by hand. This ranks them together and shows a few lines of each.

With the judge's rows for a candidate (`rows`), it also ranks matched functions by the *shape* of the mismatched
region: the target's mnemonics around the differing rows (a clamp, a cursor write, a helper call idiom), looked for in
every matched function's assembly as runs of four. Such a function was written by someone who already found the C
for that idiom; its C is shown around the place the run sits (by position, a lead). Whole-function look-alikes
(`similar`) miss these when the rest of the code differs.

ROM mode only: it reads the cross-references the relocations give (`xrefs`), the instruction look-alikes (`similar`)
and the functions' assembly text, which project mode does not store (ROADMAP.md). The shape search reads mnemonics
only, so it reaches every ROM console with assembly text (N64, PS1, PS2, PSP). Read-only."""
from __future__ import annotations

import json
import re

from ..forks.csource import find

LIMIT = 4
SNIPPET_LINES = 10
WEIGHTS = {'caller': 1.0, 'callee': 1.0, 'before': 0.8, 'after': 0.8}
SHAPE_RUN = 4          # mnemonics in a row that make one shape key
SHAPE_CONTEXT = 4      # rows of context on each side of a differing row
SHAPE_MIN = 0.6        # share of the region's runs a matched function must hold
SHAPE_MAX_INSNS = 3000  # larger matched functions are skipped (their assembly is long to read for every query)
SHAPE_WEIGHT = 2.5
MNEMONIC = re.compile(r'^\s*(?:/\*.*?\*/\s*)?([a-z][a-z0-9_.]*)\b')


def mnemonics(asm) -> list[str]:
    """The mnemonics of assembly text in order (labels, directives and comments-only lines skipped)."""
    out = []
    for line in (asm or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(':') or stripped.startswith(('.', 'glabel', 'endlabel', '#', '/*')) and \
                '*/' not in stripped:
            continue
        hit = MNEMONIC.match(line)
        if hit and hit[1] not in ('glabel', 'endlabel', 'dlabel', 'jlabel', 'nonmatching'):
            out.append(hit[1])
    return out


def region(rows, context=SHAPE_CONTEXT) -> list[str]:
    """The target's mnemonics of the mismatched region(s): every differing row with `context` rows around it."""
    keep = set()
    for i, row in enumerate(rows or ()):
        if row and row[0] != ' ':
            keep.update(range(max(0, i - context), i + context + 1))
    out = []
    for i in sorted(keep):
        if i < len(rows) and rows[i][1]:
            hit = MNEMONIC.match(str(rows[i][1]))
            if hit:
                out.append(hit[1])
    return out


def _runs(ops, n=SHAPE_RUN):
    return {tuple(ops[i:i + n]) for i in range(len(ops) - n + 1)}


def shape_alike(net, proj, fn, rows, top=4) -> list[tuple[dict, float, float]]:
    """[(matched row, share of the region's runs it holds, where in it the first run sits 0..1)]: matched functions
    holding the mismatched region's instruction shape, best first."""
    want = _runs(region(rows))
    if len(want) < 2:
        return []
    store = net.project_store(proj['id'])
    found = []
    for row in store.all("SELECT name, vram, n_insns, asm, matched_source FROM functions WHERE project_id=? AND "
                         "status='matched' AND matched_source IS NOT NULL AND n_insns<=? AND vram IS NOT ?",
                         proj['id'], SHAPE_MAX_INSNS, fn['vram']):
        ops = mnemonics(row['asm'])
        have = _runs(ops)
        share = len(want & have) / len(want)
        if share >= SHAPE_MIN:
            first = next((i for i in range(len(ops) - SHAPE_RUN + 1) if tuple(ops[i:i + SHAPE_RUN]) in want), 0)
            found.append((row, share, first / max(1, len(ops))))
    found.sort(key=lambda item: (-item[1], item[0]['n_insns'] or 0))
    return found[:top]


def region_snippet(source, name, at, lines=SNIPPET_LINES) -> list[str]:
    """`lines` lines of `name`'s C around the fraction `at` of its body (where the matching instructions sit)."""
    span = find(source, name)
    text = source[span[0]:span[1]] if span else source
    body = [line.rstrip() for line in text.strip('\n').splitlines()]
    if len(body) <= lines:
        return body
    start = min(max(0, round(at * len(body)) - lines // 3), len(body) - lines)
    return ([f'    ... ({start} lines above)'] if start else []) + body[start:start + lines] + (
        [f'    ... ({len(body) - start - lines} more lines: get_function(function="{name}", section="matched"))']
        if start + lines < len(body) else [])


def gather(net, proj, fn, limit=LIMIT, rows=None) -> list[dict]:
    """[{name, vram, n_insns, relations [text], source, at}]: matched neighbours of `fn`, closest first (`rows`: the
    judge's rows for a candidate, which add the functions sharing the mismatched region's shape)."""
    if fn['vram'] is None:
        return []
    x = net.xrefs(proj)
    store = net.project_store(proj['id'])
    score, relations = {}, {}

    def add(vram, text, weight):
        if vram is None or vram == fn['vram']:
            return
        relations.setdefault(vram, []).append(text)
        score[vram] = score.get(vram, 0.0) + weight
    for vram in x['called_by'].get(fn['vram'], ()):
        add(vram, 'calls it', WEIGHTS['caller'])
    for vram in x['calls'].get(fn['vram'], ()):
        add(vram, 'called by it', WEIGHTS['callee'])
    before = store.one('SELECT vram FROM functions WHERE project_id=? AND unit=? AND vram<? ORDER BY vram DESC LIMIT 1',
                       proj['id'], fn['unit'], fn['vram'])
    after = store.one('SELECT vram FROM functions WHERE project_id=? AND unit=? AND vram>? ORDER BY vram LIMIT 1',
                      proj['id'], fn['unit'], fn['vram'])
    if before:
        add(before['vram'], 'just before it', WEIGHTS['before'])
    if after:
        add(after['vram'], 'just after it', WEIGHTS['after'])
    shared = {}
    for address in x['uses'].get(fn['vram'], ()):
        users = x['used_by'].get(address, ())
        if len(users) > 40:  # a global everything reads says nothing about this function
            continue
        for vram in users:
            shared[vram] = shared.get(vram, 0) + 1
    for vram, n in shared.items():
        if n >= 2:
            add(vram, f'uses {n} of the same globals', min(n, 5) * 0.2)
    try:
        alike = net.similar(proj, fn, top=6)
    except Exception:  # noqa: BLE001  (a console without instruction look-alikes still has the rest)
        alike = []
    for row, sim in alike:
        if sim >= 0.5:
            add(row['vram'], f'{sim * 100:.0f}% similar code', sim * 3)
    at = {}
    for row, share, where in (shape_alike(net, proj, fn, rows) if rows else ()):
        add(row['vram'], f'same instruction shape as the mismatched region ({share * 100:.0f}% of its runs)',
            share * SHAPE_WEIGHT)
        at[row['vram']] = where
    if not score:
        return []
    found = {r['vram']: r for r in store.all(
        "SELECT name, vram, n_insns, matched_source FROM functions WHERE project_id=? AND status='matched' AND vram IN "
        "(SELECT value FROM json_each(?))", proj['id'], json.dumps(sorted(score)))}
    ranked = sorted((v for v in score if v in found and found[v]['matched_source']), key=lambda v: (-score[v], v))
    return [{'name': found[v]['name'], 'vram': v, 'n_insns': found[v]['n_insns'], 'relations': relations[v],
             'source': found[v]['matched_source'], 'at': at.get(v)} for v in ranked[:limit]]


def snippet(source, name, lines=SNIPPET_LINES) -> list[str]:
    """The first `lines` lines of `name`'s definition in `source` (the whole source's head when it can't be found)."""
    span = find(source, name)
    text = source[span[0]:span[1]] if span else source
    out = [line.rstrip() for line in text.strip('\n').splitlines()]
    return out[:lines] + ([f'    ... ({len(out) - lines} more lines: get_function(function="{name}", section="matched"))']
                          if len(out) > lines else [])


def render(items, lines=SNIPPET_LINES) -> list[str]:
    if not items:
        return []
    out = ['', '### Matched neighbours (closest first: their matching C shows this code\'s habits)']
    for item in items:
        out.append(f"- {item['name']} (0x{item['vram']:08X}, {item['n_insns']} insns): {', '.join(item['relations'])}")
        shown = (region_snippet(item['source'], item['name'], item['at'], lines) if item.get('at') is not None
                 else snippet(item['source'], item['name'], lines))
        out += ['```c', *shown, '```']
    return out


def brief(net, proj, fn, shown=(), limit=LIMIT) -> list[str]:
    """The brief's section: neighbours other than those it already shows in full (`shown`: names)."""
    items = [item for item in gather(net, proj, fn, limit + len(shown)) if item['name'] not in set(shown)][:limit]
    return render(items, lines=6)
