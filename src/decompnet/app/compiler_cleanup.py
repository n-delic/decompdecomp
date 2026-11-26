"""Consolidate rare ROM compiler profiles only after exact byte verification."""
from __future__ import annotations

import copy
from collections import Counter
from concurrent.futures import as_completed

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for, function_compiler_key


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


def move_function(config, fn, end, target):
    """Give one function `target`. Its own saved setting wins over any range, so that is what changes
    when it has one (dropped when the range or default underneath already says `target`)."""
    profiles = config.get('function_compilers') or {}
    row = dict(fn)
    offset, address = row.get('rom_offset'), row['vram']
    keys = [function_compiler_key(row), str(offset)] if offset is not None else [
        key for key, value in profiles.items() if value.get('function') == row.get('name')]
    keys = [key for key in keys if key in profiles and profiles[key].get('vram') == address]
    if not keys:
        replace_range(config, address, end, target)
        return
    rest = {key: value for key, value in profiles.items() if key not in keys}
    if profile(compiler_for({**config, 'function_compilers': rest}, address, row)) != target:
        rest[keys[0]] = {**profiles[keys[0]], 'toolchain': target[0], 'flags': list(target[1])}
    config['function_compilers'] = rest


def _chosen(selected):
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(flag, str) for flag in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    return profile(selected)


def _pair(config, counts, source_profile, target_profile):
    """The profile to move matches from and the one to move them to (none given: the project default)."""
    source = _chosen(source_profile)
    target = profile(config['compiler']) if target_profile is None else _chosen(target_profile)
    if source == profile(config['compiler']) or source not in counts:
        raise NetworkError('That non-default compiler profile is no longer in use. Refresh the list.')
    if source == target:
        raise NetworkError('Choose a different compiler profile to compare against.')
    if toolchains.installed(target[0]) is None:
        raise NetworkError('Install the project default compiler before comparing this profile.' if target_profile is None
                           else 'Install the compiler you are comparing against first.')
    return source, target


def compare(net, slug, source_profile, target_profile=None, log=print):
    """Read-only: which of one profile's matches are also byte-identical with another profile."""
    net._rom_only(slug, 'comparing compiler profiles')
    check_cancel = getattr(log, 'check_cancel', lambda: None)
    report = getattr(log, 'progress', lambda value: None)
    report({'phase': 'Reading project metadata'})
    with net.progress.view():
        proj = net._project(slug)
    config = net.cfg(proj)
    store = net.project_store(proj['id'])
    rows = store.all('SELECT * FROM functions WHERE project_id=? AND vram IS NOT NULL ORDER BY vram', proj['id'])
    addresses = Counter(fn['vram'] for fn in rows)
    current = {fn['id']: profile(compiler_for(config, fn['vram'], fn)) for fn in rows}
    source, target = _pair(config, set(current.values()), source_profile, target_profile)
    group = [fn for fn in rows if current[fn['id']] == source and fn['status'] == 'matched']
    claimed = {r['function_id'] for r in store.all('SELECT function_id FROM claims')}
    state = {'total': len(group), 'checked': 0, 'passed': 0, 'failed': 0, 'matches': [], 'failures': []}

    def publish(phase='Comparing matches'):
        report({**state, 'matches': list(state['matches']), 'failures': list(state['failures']), 'phase': phase})

    def record(name, reason='', category=None):
        state['function'] = name
        state['checked'] += 1
        state['failed' if reason else 'passed'] += 1
        if reason:
            state['failures'].append({'function': name, 'reason': reason, **({'category': category} if category else {})})
        else:
            state['matches'].append(name)
        publish()
        if state['checked'] % 25 == 0 or state['checked'] == state['total']:
            log(f"{state['checked']}/{state['total']} checked: {state['passed']} can move, {state['failed']} cannot.")

    def check(fn):
        check_cancel()
        try:
            ok, diff, _ = net._judge_with(proj, fn, target[0], list(target[1]), fn['matched_source'])
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            return fn, str(error)[:300]
        return fn, '' if ok else 'Does not compile or compare.' if diff is None else f'Different bytes (score {diff.score}).'

    publish()
    log(f"Comparing {len(group)} matches against {target[0]} {' '.join(target[1])}. No settings will change.")
    with net._pool.group('Compiler profile comparison', slug) as pool:
        pending = []
        try:
            for fn in group:
                if not fn['matched_source']:
                    record(fn['name'], 'No saved C is available.', 'no_source')
                elif addresses[fn['vram']] != 1:
                    record(fn['name'], 'Shares its address with another overlay.', 'overlay')
                elif fn['id'] in claimed:
                    record(fn['name'], 'Claimed right now.', 'busy')
                else:
                    pending.append(pool.submit(check, fn))
            for future in as_completed(pending):
                check_cancel()
                fn, reason = future.result()
                record(fn['name'], reason)
        finally:
            for future in pending:
                future.cancel()
    log('Comparison finished. Settings and matches are unchanged.')
    return state


def cleanup(net, slug, log=print, source_profile=None, target_profile=None):
    """Find the fewest compiler profiles that keep every match byte-identical.

    Fewest profiles is a set-cover problem, so this is a greedy search: profiles are tried for
    elimination from the least used up. A profile goes only when *every* function that uses it
    can move to a profile that stays, and each moved match is judged byte for byte on its own C.
    Partly emptying a profile saves no bucket, so it is never done (except for one profile the
    user picked, where each function moves on its own, to the default or to the profile they chose)."""
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
            selected, destination = _pair(config, counts, source_profile, target_profile)
            sources = [selected]
            members = sum(1 for fn in rows if current[fn['id']] == selected and fn['status'] == 'matched')
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
            move_function(proposed, fn, end, target)
            changed.append({'function': fn['name'], 'from': list(current[fn['id']]), 'to': list(target)})
            current[fn['id']] = target

        log(f'Looking for the fewest compiler profiles: {len(sources)} non-default profiles to try removing, least used first.')
        for number, source in enumerate(sources, 1):
            check_cancel()
            group = [fn for fn in rows if current[fn['id']] == source]
            if not group:
                continue
            live = Counter(current.values())
            targets = [destination] if partial else [default] + sorted((p for p in live if p not in (source, default)), key=lambda p: (-live[p], p))
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
        unchanged = (members - len(changed) - skipped if partial else
                     sum(1 for fn in rows if fn['status'] == 'matched' and profile(compiler_for(config, fn['vram'], fn)) != default) - len(changed) - skipped)
        return {'changed': changed, 'tested': tested, 'skipped': skipped, 'profiles_before': len(counts), 'profiles_after': len(after), 'ranges_before': len(config.get('compiler_ranges', [])),
                'ranges_after': len(proposed.get('compiler_ranges', [])), 'unchanged': max(0, unchanged)}
