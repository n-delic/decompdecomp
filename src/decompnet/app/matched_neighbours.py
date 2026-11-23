"""The matched functions closest to one function, with a snippet of their matching C (the brief, the skill's orient).

An agent starting on a function reads the closest matched code first when it knows where it is: a caller that shows
how the result is used, a callee whose prototype it needs, the function before and after it (one source file,
often one author's habits: the same temporaries, the same loop shapes), one that reads the same globals, one whose
instructions look alike. The brief listed callers and callees by name and look-alikes by score, but never the matched
C of the others, so agents searched for them by hand. This ranks them together and shows a few lines of each.

ROM mode only: it reads the cross-references the relocations give (`xrefs`) and the instruction look-alikes
(`similar`), which project mode does not store (ROADMAP.md). Read-only."""
from __future__ import annotations

import json

from ..forks.csource import find

LIMIT = 4
SNIPPET_LINES = 10
WEIGHTS = {'caller': 1.0, 'callee': 1.0, 'before': 0.8, 'after': 0.8}


def gather(net, proj, fn, limit=LIMIT) -> list[dict]:
    """[{name, vram, n_insns, relations [text], source}]: matched neighbours of `fn`, closest first."""
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
    if not score:
        return []
    rows = {r['vram']: r for r in store.all(
        "SELECT name, vram, n_insns, matched_source FROM functions WHERE project_id=? AND status='matched' AND vram IN "
        "(SELECT value FROM json_each(?))", proj['id'], json.dumps(sorted(score)))}
    ranked = sorted((v for v in score if v in rows and rows[v]['matched_source']), key=lambda v: (-score[v], v))
    return [{'name': rows[v]['name'], 'vram': v, 'n_insns': rows[v]['n_insns'], 'relations': relations[v],
             'source': rows[v]['matched_source']} for v in ranked[:limit]]


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
        out += ['```c', *snippet(item['source'], item['name'], lines), '```']
    return out


def brief(net, proj, fn, shown=(), limit=LIMIT) -> list[str]:
    """The brief's section: neighbours other than those it already shows in full (`shown`: names)."""
    items = [item for item in gather(net, proj, fn, limit + len(shown)) if item['name'] not in set(shown)][:limit]
    return render(items, lines=6)
