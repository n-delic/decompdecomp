"""Consolidate rare ROM compiler profiles only after exact byte verification."""
from __future__ import annotations

import copy
from collections import Counter

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for


def profile(config):
    return config['toolchain'], tuple(config.get('flags') or [])


def replace_range(config, start, end, target):
    """Override just this function, retaining the settings on either side."""
    kept = []
    for item in config.get('compiler_ranges', []):
        for low, high in ((item['start'], min(item['end'], start)), (max(item['start'], end), item['end'])):
            if low < high:
                kept.append(dict(item, start=low, end=high))
    if target != profile(config['compiler']):
        kept.append({'start': start, 'end': end, 'toolchain': target[0], 'flags': list(target[1])})
    config['compiler_ranges'] = sorted(kept, key=lambda item: item['start'])


def cleanup(net, slug, rare_limit=3, log=print, source_profile=None):
    net._rom_only(slug, 'compiler profile cleanup')
    if not 1 <= rare_limit <= 25:
        raise NetworkError('Choose a rare-profile limit between 1 and 25 functions.')
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    # Serialize with drafts and progress imports; read-only UI remains responsive.
    with net.progress.judging(slug):
        proj = net._project(slug)
        config = net.cfg(proj)
        proposed = copy.deepcopy(config)
        rows = net.store.all('SELECT * FROM functions WHERE project_id=? AND vram IS NOT NULL ORDER BY vram', proj['id'])
        addresses = Counter(fn['vram'] for fn in rows)
        starts = sorted(addresses)
        counts = Counter(profile(compiler_for(config, fn['vram'])) for fn in rows)
        default = profile(config['compiler'])
        if source_profile is not None:
            if not isinstance(source_profile, dict) or not isinstance(source_profile.get('toolchain'), str) or not isinstance(source_profile.get('flags'), list) or not all(isinstance(flag, str) for flag in source_profile['flags']):
                raise NetworkError('Choose a compiler profile from the current list.')
            selected = profile(source_profile)
            if selected == default or selected not in counts:
                raise NetworkError('That non-default compiler profile is no longer in use. Refresh the list.')
            if toolchains.installed(default[0]) is None:
                raise NetworkError('Install the project default compiler before comparing this profile.')
            common = [default]
        else:
            selected = None
            common = [p for p, _ in counts.most_common(3) if toolchains.installed(p[0]) is not None]
        candidates = [fn for fn in rows if fn['status'] == 'matched' and fn['matched_source']
                      and profile(compiler_for(config, fn['vram'])) != profile(config['compiler'])
                      and (profile(compiler_for(config, fn['vram'])) == selected if selected else counts[profile(compiler_for(config, fn['vram']))] <= rare_limit)]
        changed, tested, skipped = [], 0, 0
        if selected:
            log(f'Checking all {len(candidates)} matched functions from the selected profile against the project default only.')
        else:
            log(f'Checking {len(candidates)} matched functions from profiles used by at most {rare_limit} functions. Testing up to three more-common installed profiles.')
        for index, fn in enumerate(candidates, 1):
            check_cancel()
            if addresses[fn['vram']] > 1 or net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
                skipped += 1
                log(f'{index}/{len(candidates)}: {fn["name"]} is busy or shares an overlay address; skipped.')
                continue
            own = profile(compiler_for(config, fn['vram']))
            for target in common:
                if target == own or (selected is None and counts[target] <= counts[own]):
                    continue
                check_cancel()
                log(f'{index}/{len(candidates)}: {fn["name"]} — {target[0]} {" ".join(target[1])}')
                ok, _, _ = net._judge_with(proj, fn, target[0], list(target[1]), fn['matched_source'])
                tested += 1
                if ok:
                    end = min([fn['vram'] + max(1, fn['size'])] + [v for v in starts if v > fn['vram']])
                    replace_range(proposed, fn['vram'], end, target)
                    changed.append({'function': fn['name'], 'from': list(own), 'to': list(target)})
                    break
        check_cancel()
        # Redundant default overrides and adjoining identical ranges need no code change.
        compact = []
        for item in sorted(proposed.get('compiler_ranges', []), key=lambda r: r['start']):
            if profile(item) == profile(proposed['compiler']):
                continue
            if compact and compact[-1]['end'] == item['start'] and profile(compact[-1]) == profile(item):
                compact[-1]['end'] = item['end']
            else:
                compact.append(dict(item))
        proposed['compiler_ranges'] = compact
        if changed or proposed.get('compiler_ranges', []) != config.get('compiler_ranges', []):
            # Recheck every affected match and reject concurrent changes before the atomic commit.
            commit_config(net, proj, proposed, log)
            with net.store.tx() as db:
                net._event(db, proj['id'], None, 'compiler', f'Compiler cleanup moved {len(changed)} functions to more common settings; all affected matches verified.')
        after = {profile(compiler_for(proposed, fn['vram'])) for fn in rows}
        log(f'Cleanup complete: {len(changed)} functions moved; {len(counts)} → {len(after)} used profiles. Other functions keep their settings.')
        return {'changed': changed, 'tested': tested, 'skipped': skipped, 'profiles_before': len(counts), 'profiles_after': len(after), 'ranges_before': len(config.get('compiler_ranges', [])),
                'ranges_after': len(proposed.get('compiler_ranges', [])), 'unchanged': len(candidates) - len(changed) - skipped}


def commit_config(net, proj, proposed, log=print):
    """Verify affected functions against private settings before an atomic guarded commit."""
    import json

    before = net.cfg(proj)
    rows = net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id', proj['id'])
    affected = [f for f in rows if compiler_for(before, f['vram']) != compiler_for(proposed, f['vram'])]
    trial = dict(proj) | {'config': json.dumps(proposed)}
    contexts = [tuple(r) for r in net.store.all('SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version', proj['id'])]
    symbols = [tuple(r) for r in net.store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    report = getattr(log, 'progress', lambda value: None)
    scores = {}
    for index, fn in enumerate(affected, 1):
        check_cancel()
        own = compiler_for(proposed, fn['vram'])
        source = fn['matched_source'] if fn['status'] == 'matched' else net._source_of(fn)
        ok, diff, _ = net._judge_with(trial, fn, own['toolchain'], list(own['flags']), source) if source else (False, None, '')
        if fn['status'] == 'matched' and not ok:
            raise NetworkError(f"{fn['name']} would no longer match. Keep its compiler settings and retry.")
        if fn['status'] == 'open':
            scores[fn['id']] = diff.score if diff is not None else None
        report({'done': index, 'total': len(affected), 'phase': 'Verifying compiler settings', 'function': fn['name']})
    check_cancel()
    with net.progress._project_lock(proj['slug']), net.store.tx() as db:
        current = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()['config'])
        latest = db.execute('SELECT * FROM functions WHERE project_id=? ORDER BY id', (proj['id'],)).fetchall()
        headers = [tuple(r) for r in db.execute('SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version', (proj['id'],))]
        latest_symbols = [tuple(r) for r in db.execute('SELECT * FROM symbols WHERE project_id=? ORDER BY name', (proj['id'],))]
        if any(current.get(k) != before.get(k) for k in ('compiler', 'compiler_ranges', 'gp', 'big_endian', 'rom', 'platform')) or [tuple(f) for f in latest] != [tuple(f) for f in rows] or headers != contexts or latest_symbols != symbols:
            raise NetworkError('Code or settings changed during verification. Retry after the other work finishes.')
        for fn in affected:
            if fn['status'] == 'open' and db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
                raise NetworkError(f"An agent is working on {fn['name']}. Retry after it finishes.")
        current['compiler'] = proposed['compiler']
        current['compiler_ranges'] = proposed.get('compiler_ranges', [])
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(current), proj['id']))
        for fid, score in scores.items():
            db.execute('UPDATE functions SET best_score=?,seed=NULL WHERE id=?', (score, fid))
        net._event(db, proj['id'], None, 'compiler', 'Verified compiler profile changes saved')
    net.bump_generation(proj['id'])
    net.progress.sync(proj['slug'], force=True)
