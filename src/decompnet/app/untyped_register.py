"""Best C that a permuter bug left changed in meaning, put back to the attempt before it.

decomp-repair's permuter read `D_80102ED4 = 0;` as a declaration (type `D_80102ED`, name `4`) and toggled `register`
onto it: `register D_80102ED4 = 0;` declares a new local where the C stored to a global. Dropping the store raised the
byte score, so 35 functions of an N64 game kept it as their best, and every correct candidate scores worse than that
(fixed in decomp-repair 0.5.11). A declaration with `register` and no type is never in C anyone wrote, so before a deep
pass searches from such a best, the function's best goes back to its best attempt without one, or to that C with the
stray `register` taken out, judged. The same holds for the other bugs `decomprepair.debris.clean` takes out (0.5.16-17): a
pointer cut to 16 bits inside a pointer cast, casts on type names, narrowed hex literals (`(unsigned short)0xDA380003`
storing 3 where the game stored a display-list command; 12 bests in that game).
"""
import re

UNTYPED = re.compile(r'(?<![\w.])register\s+[A-Za-z_]\w*\s*=(?!=)')


def damaged(source: str | None) -> bool:
    from decomprepair import debris
    return bool(source) and (bool(UNTYPED.search(source)) or debris.clean(source, unwrap_do_zero=False) is not None)


def restore(net, proj, fn):
    """`fn` with its best put back to the best clean attempt when its best holds an untyped `register`; else `fn`."""
    if fn['status'] != 'open' or not damaged(fn['best_source']):
        return fn
    store = net.project_store(proj['id'])
    rows = store.all('SELECT score, source FROM attempt_details WHERE function_id=? AND compiled=1 AND score IS NOT NULL '
                     'ORDER BY score, id', fn['id'])
    clean = next((r for r in rows if r['source'] and not damaged(r['source'])), None)
    if clean is not None:
        score, source = clean['score'], clean['source']
    elif not hasattr(net, '_judge_with'):
        return fn  # (a stand-in network that can't judge: leave the best as it is)
    else:
        # (most such bests have no clean attempt left: the C with the stray `register` taken out, judged as an attempt
        # would be; a lower stored score than the C's real one would make the search reject every true improvement)
        from decomprepair import debris
        source = debris.clean(fn['best_source'], unwrap_do_zero=False) or fn['best_source']
        from ..project import compiler_for
        own = compiler_for(net.cfg(proj), fn['vram'], fn)
        flags = own['flags'].split() if isinstance(own['flags'], str) else list(own['flags'])
        exact, diff, _ = net._judge_with(proj, fn, own['toolchain'], flags, source)
        if diff is None:
            return fn
        score = 0 if exact else max(1, diff.score)
    with store.tx() as db:
        # (guarded on the score the pass read: the stored C is kept by reference, so it can't be compared as text)
        db.execute('UPDATE functions SET best_score=?, best_source=? WHERE id=? AND status=? AND best_score=?',
                   (score, source, fn['id'], 'open', fn['best_score']))
    return {**dict(fn), **dict(net._function(fn['name'], proj['slug']))}
