"""Recheck function-only compiler settings that nothing ever proved.

Until the compiler comparison stopped doing so, its auto-apply gave a function its own settings whenever they scored
a little better on the function's current C, which was usually m2c's draft. A draft follows whatever settings its
shape happens to suit: in an N64 game 302 functions got their own settings that way (median +2.9 points), one of them
`-O1` and another `-fno-schedule-insns -g3` where the game used the project's `-O2`. A deep pass then climbs on the
wrong settings and rejects the steps toward the real C.

A setting is verified when its function matches with it, or when a comparison saw that exact profile match. Every
other one is rechecked: the function's current C is judged with its own settings and with what it would get without
them. An exact result settles it (and the function is matched). Otherwise the setting goes, because a score on
unmatched C is no evidence, but it is kept as a hypothesis (`unverified_function_compilers`): the deep pass still
tries it for an exact match, and Undo puts it back. Changes are committed in pages through `_apply_compiler_config`,
which re-verifies every affected match and rescores the rest, so Stop keeps what was done.
"""
from __future__ import annotations

import copy
import json
import time

from ..network import NetworkError
from ..project import compiler_for, function_compiler_key

HYPOTHESES = 'unverified_function_compilers'
PAGE = 25


def _within(log, report, done, total):
    """The page's own checks (affected matches, rescoring) as a step of the overall count, not a new bar."""
    def inner(line):
        log(line)
    inner.check_cancel = getattr(log, 'check_cancel', lambda: None)
    inner.progress = lambda value: report({'phase': 'Saving: ' + str(value.get('phase', 'checking')).lower(),
                                           'done': done, 'total': total})
    return inner


def _profile(entry):
    return entry['toolchain'], tuple(entry.get('flags') or [])


def _exact_seen(net, proj, fn, entry) -> bool:
    """A comparison recorded this exact profile matching this function's C."""
    row = net.project_store(proj['id']).one('SELECT result FROM compiler_tests WHERE function_id=?', fn['id'])
    if not row:
        return False
    try:
        result = json.loads(row['result'])
    except (TypeError, ValueError):
        return False
    return any(r.get('exact') and (r.get('toolchain'), tuple(r.get('flags') or [])) == _profile(entry)
               for r in result.get('ranked') or [])


def unverified(net, proj) -> list[tuple[str, dict, dict]]:
    """(key, entry, function) for each function-only setting that no match or exact comparison supports."""
    cfg = net.cfg(proj)
    entries = cfg.get('function_compilers') or {}
    if not entries:
        return []
    rows = net.project_store(proj['id']).all(
        'SELECT id,name,vram,rom_offset,size,status,unit FROM functions WHERE project_id=? AND vram IS NOT NULL', proj['id'])
    by_key = {}
    for fn in rows:
        key = function_compiler_key(fn)
        if key in entries:
            by_key[key] = fn
    found = []
    for key, entry in entries.items():
        fn = by_key.get(key)
        if fn is None or fn['status'] in ('matched', 'asm') or _exact_seen(net, proj, fn, entry):
            continue
        found.append((key, entry, fn))
    return found


def summary(net, slug) -> dict:
    proj = net._project(slug)
    return {'unverified': len(unverified(net, proj)),
            'hypotheses': len(net.cfg(proj).get(HYPOTHESES) or {})}


def recheck(net, slug, log=print) -> dict:
    net._rom_only(slug, 'rechecking function compiler settings')
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    report = getattr(log, 'progress', lambda value: None)
    proj = net._project(slug)
    todo = unverified(net, proj)
    total = len(todo)
    log(f'Rechecking {total} function settings that no match supports.')
    outcome = {'total': total, 'reset': [], 'kept': [], 'matched': [], 'skipped': [], 'failed': []}
    report({'phase': 'Rechecking function settings', 'done': 0, 'total': total})
    done = 0
    for start in range(0, total, PAGE):
        check_cancel()
        page = todo[start:start + PAGE]
        proj = net._project(slug)
        config = net.cfg(proj)
        proposed = copy.deepcopy(config)
        removed, exact = {}, []
        for key, entry, fn in page:
            check_cancel()
            done += 1
            fresh = net._function(fn['name'], slug)
            current = (config.get('function_compilers') or {}).get(key)
            if current != entry or fresh['status'] != 'open':
                outcome['skipped'].append({'function': fn['name'], 'reason': 'changed meanwhile'})
                continue
            if net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
                outcome['skipped'].append({'function': fn['name'], 'reason': 'an agent is working on it'})
                continue
            source = net._comparison_source(fresh)
            if not source:
                outcome['skipped'].append({'function': fn['name'], 'reason': 'no saved C'})
                continue
            without = copy.deepcopy(config)
            without['function_compilers'].pop(key, None)
            base = compiler_for(without, fresh['vram'], fresh)
            own_ok, own_diff, _ = net._judge_with(proj, fresh, entry['toolchain'], list(entry['flags']), source)
            base_ok, base_diff, _ = net._judge_with(proj, fresh, base['toolchain'], list(base['flags']), source)
            scores = {'own': own_diff.score if own_diff is not None else None,
                      'without': base_diff.score if base_diff is not None else None}
            row = {'function': fn['name'], 'from': list(_profile(entry)), 'to': list(_profile(base)), **scores}
            if own_ok and not base_ok:
                outcome['kept'].append(row)
                exact.append((fresh, source))
                log(f'{done}/{total} {fn["name"]}: matches with its own settings; kept.')
                continue
            proposed['function_compilers'].pop(key, None)
            removed[key] = {**entry, 'reset_at': time.time(), **scores}
            if base_ok:
                exact.append((fresh, source))
            outcome['reset'].append(row)
            log(f'{done}/{total} {fn["name"]}: ' + ('matches without its own settings; removed.' if base_ok else
                f'no exact match either way ({scores["own"]} with its own, {scores["without"]} without); '
                'removed and kept as a hypothesis.'))
            report({'phase': 'Rechecking function settings', 'done': done, 'total': total, 'function': fn['name']})
        if removed:
            try:
                net._apply_compiler_config(proj, proposed, log=_within(log, report, done, total))
            except NetworkError as error:
                # (an agent changed code meanwhile: these stay as they were; a later run picks them up again)
                for key in removed:
                    name = removed[key].get('function')
                    outcome['reset'] = [r for r in outcome['reset'] if r['function'] != name]
                    outcome['failed'].append({'function': name, 'reason': str(error)})
                log(f'This page was not saved: {error}')
                continue

            def keep(cfg, removed=removed):
                cfg.setdefault(HYPOTHESES, {}).update(removed)
            net._update_cfg(slug, keep)
        for fresh, source in exact:
            agent, _ = net.local_agent('autopilot')
            result = net.try_quietly(agent, net._project(slug), net._function(fresh['name'], slug), source,
                                     synchronize=False, exact_only=True)
            if result.get('matched'):
                outcome['matched'].append(fresh['name'])
        report({'phase': 'Rechecking function settings', 'done': done, 'total': total})
    with net.project_store(net._project(slug)['id']).tx() as db:
        net._event(db, net._project(slug)['id'], None, 'compiler',
                   f"Rechecked {total} unverified function settings: {len(outcome['reset'])} removed (kept as hypotheses), "
                   f"{len(outcome['kept'])} kept, {len(outcome['matched'])} matched.")
    log(f"Done: {len(outcome['reset'])} removed and kept as hypotheses, {len(outcome['kept'])} kept, "
        f"{len(outcome['matched'])} newly matched, {len(outcome['skipped'])} skipped, {len(outcome['failed'])} not saved.")
    return outcome


def undo(net, slug, log=print) -> dict:
    """Put the removed settings back, for open functions that have no setting of their own again since."""
    net._rom_only(slug, 'restoring function compiler settings')
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    proj = net._project(slug)
    config = net.cfg(proj)
    hypotheses = dict(config.get(HYPOTHESES) or {})
    proposed = copy.deepcopy(config)
    restored, skipped = [], []
    rows = net.project_store(proj['id']).all('SELECT name,vram,rom_offset,status FROM functions WHERE project_id=?', proj['id'])
    status = {function_compiler_key(r): r['status'] for r in rows}
    for key, entry in hypotheses.items():
        check_cancel()
        # (one with settings of its own again keeps them; one matched since is proven with what it has now)
        if key in (proposed.get('function_compilers') or {}) or status.get(key) != 'open':
            skipped.append(entry.get('function'))
            continue
        proposed.setdefault('function_compilers', {})[key] = {k: entry[k] for k in ('vram', 'function', 'toolchain', 'flags')}
        restored.append(entry.get('function'))
    if restored:
        log(f'Restoring {len(restored)} function settings; every affected match is checked first.')
        net._apply_compiler_config(proj, proposed, log=log)

    def drop(cfg):
        cfg.pop(HYPOTHESES, None)
    net._update_cfg(slug, drop)
    log(f'Restored {len(restored)} function settings; {len(skipped)} matched or have other settings now and keep them.')
    return {'restored': restored, 'skipped': skipped}


def hypothesis(cfg, fn) -> dict | None:
    """The removed setting the deep pass still probes for an exact match, as a profile."""
    entry = (cfg.get(HYPOTHESES) or {}).get(function_compiler_key(fn)) if fn is not None else None
    return {'toolchain': entry['toolchain'], 'flags': list(entry['flags'])} if entry else None
