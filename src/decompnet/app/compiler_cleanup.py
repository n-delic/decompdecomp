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


def cleanup(net, slug, log=print, source_profile=None):
    """Find the fewest compiler profiles that keep every match byte-identical.

    Fewest profiles is a set-cover problem, so this is a greedy search: profiles are tried for
    elimination from the least used up. A profile goes only when *every* function that uses it
    can move to a profile that stays, and each moved match is judged byte for byte on its own C.
    Partly emptying a profile saves no bucket, so it is never done (except for one profile the
    user picked, where each function moves on its own)."""
    net._rom_only(slug, 'compiler profile cleanup')
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    # Serialize with drafts and progress imports; read-only UI remains responsive.
    with net.progress.judging(slug):
        proj = net._project(slug)
        config = net.cfg(proj)
        proposed = copy.deepcopy(config)
        rows = net.project_store(proj['id']).all('SELECT * FROM functions WHERE project_id=? AND vram IS NOT NULL ORDER BY vram', proj['id'])
        addresses = Counter(fn['vram'] for fn in rows)
        starts = sorted(addresses)
        current = {fn['id']: profile(compiler_for(config, fn['vram'], fn)) for fn in rows}
        counts = Counter(current.values())
        default = profile(config['compiler'])
        partial = source_profile is not None
        if partial:
            if not isinstance(source_profile, dict) or not isinstance(source_profile.get('toolchain'), str) or not isinstance(source_profile.get('flags'), list) or not all(isinstance(flag, str) for flag in source_profile['flags']):
                raise NetworkError('Choose a compiler profile from the current list.')
            selected = profile(source_profile)
            if selected == default or selected not in counts:
                raise NetworkError('That non-default compiler profile is no longer in use. Refresh the list.')
            if toolchains.installed(default[0]) is None:
                raise NetworkError('Install the project default compiler before comparing this profile.')
            sources = [selected]
        else:
            sources = sorted((p for p in counts if p != default), key=lambda p: (counts[p], p))
        changed, tested, skipped, proven = [], 0, 0, {}
        skipped_ids = set()

        def movable(fn):
            return (fn['status'] == 'matched' and fn['matched_source'] and addresses[fn['vram']] == 1
                    and not net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?', fn['id']))

        def fits(fn, target):
            nonlocal tested
            key = (fn['id'], target)
            if key not in proven:
                check_cancel()
                log(f'{fn["name"]} — {target[0]} {" ".join(target[1])}')
                proven[key] = bool(net._judge_with(proj, fn, target[0], list(target[1]), fn['matched_source'])[0])
                tested += 1
            return proven[key]

        def move(fn, target):
            end = min([fn['vram'] + max(1, fn['size'])] + [v for v in starts if v > fn['vram']])
            replace_range(proposed, fn['vram'], end, target)
            changed.append({'function': fn['name'], 'from': list(current[fn['id']]), 'to': list(target)})
            current[fn['id']] = target

        log(f'Looking for the fewest compiler profiles: {len(sources)} non-default profiles to try removing, least used first.')
        for number, source in enumerate(sources, 1):
            check_cancel()
            group = [fn for fn in rows if current[fn['id']] == source]
            if not group:
                continue
            live = Counter(current.values())
            targets = [default] if partial else [default] + sorted((p for p in live if p not in (source, default)), key=lambda p: (-live[p], p))
            targets = [t for t in targets if toolchains.installed(t[0]) is not None]
            label = f'{source[0]} {" ".join(source[1])}'
            blocked = [fn for fn in group if not movable(fn)]
            skipped_ids.update(fn['id'] for fn in blocked)
            if blocked and not partial:
                log(f'Profile {number}/{len(sources)} ({label}) stays: {len(blocked)} of its {len(group)} functions are unmatched, busy or share an overlay address.')
                continue
            plan = []
            for fn in group:
                if fn in blocked:
                    continue
                target = next((t for t in targets if fits(fn, t)), None)
                if target is None and not partial:
                    plan = None
                    break
                if target is not None:
                    plan.append((fn, target))
            if plan is None:
                log(f'Profile {number}/{len(sources)} ({label}) stays: one of its matches needs it.')
                continue
            for fn, target in plan:
                move(fn, target)
            if plan:
                log(f'Profile {number}/{len(sources)} ({label}) removed: {len(plan)} functions moved.')
        skipped = len(skipped_ids)
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
            net._apply_compiler_config(proj, proposed, log=log)
            with net.project_store(proj['id']).tx() as db:
                net._event(db, proj['id'], None, 'compiler', f'Compiler cleanup moved {len(changed)} functions so fewer compiler profiles are needed; all affected matches verified.')
        after = {profile(compiler_for(proposed, fn['vram'], fn)) for fn in rows}
        log(f'Cleanup complete: {len(changed)} functions moved; {len(counts)} → {len(after)} used profiles. Other functions keep their settings.')
        return {'changed': changed, 'tested': tested, 'skipped': skipped, 'profiles_before': len(counts), 'profiles_after': len(after), 'ranges_before': len(config.get('compiler_ranges', [])),
                'ranges_after': len(proposed.get('compiler_ranges', [])), 'unchanged': sum(1 for fn in rows if fn['status'] == 'matched' and profile(compiler_for(config, fn['vram'], fn)) != default) - len(changed) - skipped}
