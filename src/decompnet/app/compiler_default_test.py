"""Read-only trial of an alternative profile against matches using the project default."""
import copy
import json
from concurrent.futures import as_completed
from hashlib import sha256

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for
from .compiler_cleanup import profile, replace_range

_SCAN_PAGE = 512
_COMPILE_BATCH = 64
_PROFILE_FIELDS = ('id', 'name', 'vram', 'rom_offset', 'status')
_INPUT_FIELDS = ('id', 'name', 'vram', 'rom_offset', 'unit', 'size', 'target', 'relocs',
                 'meta', 'status', 'matched_source', 'best_source', 'best_score')


def _profile_pages(store, project_id, cancel, *, matched_only=False):
    status = " AND status='matched'" if matched_only else ''
    cursor = -2**63
    while True:
        cancel()
        rows = store.all(f'SELECT {','.join(_PROFILE_FIELDS)} FROM functions WHERE project_id=?{status} '
                         'AND id>? ORDER BY id LIMIT ?', project_id, cursor, _SCAN_PAGE)
        if not rows:
            return
        yield rows
        cursor = rows[-1]['id']


def _function_batches(store, project_id, candidates, cancel):
    ids = list(candidates)
    for start in range(0, len(ids), _COMPILE_BATCH):
        cancel()
        batch = ids[start:start + _COMPILE_BATCH]
        placeholders = ','.join('?' for _ in batch)
        rows = store.all(f'SELECT * FROM functions WHERE project_id=? AND id IN ({placeholders})',
                         project_id, *batch)
        yield batch, {row['id']: row for row in rows}


def _signature(fn):
    # Keep only a digest between batches, including the saved C and target the judge saw.
    return sha256(repr(tuple(fn[key] for key in _INPUT_FIELDS)).encode()).digest()


def run(net, slug, selected, log=print, nonmatches=False):
    if nonmatches:
        from .compiler_nonmatch_test import run as test_nonmatches
        return test_nonmatches(net,slug,selected,log)
    report = getattr(log, 'progress', lambda value: None)
    cancel = getattr(log, 'check_cancel', lambda: None)
    cancel()
    report({'phase': 'Reading project metadata'})
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    with net.progress.view():
        net._rom_only(slug, 'testing a compiler profile as default')
        proj = net._project(slug)
    project_id = proj['id']
    store = net.project_store(project_id)
    cfg = json.loads(proj['config'])
    default, target = profile(cfg['compiler']), profile(selected)
    profile_error = 'Choose a non-default compiler profile currently used in this project.'
    if target == default:
        raise NetworkError(profile_error)

    def headers():
        return [tuple(r) for r in store.all(
            'SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version', project_id)]

    original_headers = headers()
    candidates = {}
    target_used = False
    scanned = 0
    scan_total = store.one('SELECT count(*) FROM functions WHERE project_id=?', project_id)[0]
    for rows in _profile_pages(store, project_id, cancel):
        for fn in rows:
            if target_used and fn['status'] != 'matched':
                continue
            effective = profile(compiler_for(cfg, fn['vram'], fn))
            target_used |= effective == target
            if effective == default and fn['status'] == 'matched':
                candidates[fn['id']] = tuple(fn)
        scanned += len(rows)
        report({'phase': f'Scanning compiler profiles · {scanned:,} functions scanned',
                'done': scanned, 'total': max(scanned, scan_total)})
    if not target_used:
        raise NetworkError(profile_error)
    cancel()
    report({'phase': 'Checking compiler installation'})
    if toolchains.installed(target[0]) is None:
        raise NetworkError('Install the selected compiler before testing it as default.')
    cancel()
    state = {'total':len(candidates), 'checked':0, 'passed':0, 'failed':0,
             'profile':{'toolchain':target[0], 'flags':list(target[1])},
             'default':cfg['compiler'], 'failures':[], 'matches':[], 'stale':False, 'nonmatches':False}
    def publish(phase='Testing default-profile matches'):
        report({**state, 'failures': list(state['failures']), 'matches': list(state['matches']), 'phase': phase})
    publish()
    log(f"Testing {len(candidates)} matched functions using the default against {target[0]} {' '.join(target[1])}. No settings will change.")
    def check(fn):
        cancel()
        source = fn['matched_source']
        if not source:
            return fn, False, 'No saved C is available.'
        try:
            ok, diff, _ = net._judge_with(proj, fn, target[0], list(target[1]), source)
            reason = '' if ok else 'Does not compile or compare.' if diff is None else f'Different bytes (score {diff.score}).'
            return fn, ok, reason
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            return fn, False, str(error)[:300]
    signatures = {}

    def record(name, ok, reason):
        state['function'] = name
        state['checked'] += 1
        state['passed' if ok else 'failed'] += 1
        if ok:
            state['matches'].append(name)
        else:
            state['failures'].append({'function': name, 'reason': reason})
        publish()
        if state['checked'] % 25 == 0 or state['checked'] == state['total']:
            log(f"{state['checked']}/{state['total']} checked: {state['passed']} passed, {state['failed']} did not pass.")

    with net._pool.group('Default compiler verification', slug) as pool:
        for batch, rows in _function_batches(store, project_id, candidates, cancel):
            pending = []
            try:
                for identity in batch:
                    cancel()
                    fn = rows.get(identity)
                    original = candidates[identity]
                    if fn is None or tuple(fn[key] for key in _PROFILE_FIELDS) != original:
                        record(original[1], False, 'Function changed during preparation; rerun the test.')
                        continue
                    signatures[identity] = _signature(fn)
                    pending.append(pool.submit(check, fn))
                for future in as_completed(pending):
                    cancel()
                    fn, ok, reason = future.result()
                    record(fn['name'], ok, reason)
            finally:
                for future in pending:
                    future.cancel()

    cancel()
    publish('Confirming tested inputs')
    current = store.one('SELECT config FROM projects WHERE id=?', project_id)
    current_cfg = json.loads(current['config']) if current else None
    stale = (not current_cfg or len(signatures) != len(candidates)
             or any(current_cfg.get(key) != cfg.get(key)
                    for key in ('compiler', 'compiler_ranges', 'function_compilers'))
             or headers() != original_headers)
    if not stale:
        latest_ids = {
            fn['id'] for rows in _profile_pages(store, project_id, cancel, matched_only=True)
            for fn in rows if profile(compiler_for(cfg, fn['vram'], fn)) == default
        }
        stale = latest_ids != signatures.keys()
    if not stale:
        for batch, rows in _function_batches(store, project_id, candidates, cancel):
            if any(key not in rows or _signature(rows[key]) != signatures[key] for key in batch):
                stale = True
                break
    state['stale'] = stale
    state['compatible'] = bool(candidates) and state['failed'] == 0 and not state['stale']
    log('Test finished. Settings, matches and saved scores are unchanged.' + (' Inputs changed during the test; rerun before making a decision.' if state['stale'] else ''))
    return state


def replace_default(net, slug, selected, log=print):
    """Promote a profile, retaining the previous settings wherever a saved match needs them."""
    with net.progress.judging(slug):
        proj = net._project(slug)
        tested_default = profile(json.loads(proj['config'])['compiler'])
        result = run(net, slug, selected, log)
        # Helpers keep matching during the trial (result['stale']). The commit below rechecks every match as it
        # is now, so only another default makes this a different replacement.
        proj = net._project(slug)
        cfg = json.loads(proj['config'])
        if profile(cfg['compiler']) != tested_default:
            raise NetworkError('The project default was changed during testing. Run the replacement again.')
        rows = net.project_store(proj['id']).all(
            'SELECT name,vram,rom_offset,size FROM functions WHERE project_id=? ORDER BY id', proj['id'])
        proposed = copy.deepcopy(cfg)
        old, target = profile(cfg['compiler']), profile(selected)
        proposed['compiler'] = dict(cfg['compiler'], toolchain=target[0], flags=list(target[1]))
        # Explicit old-default ranges must also follow the new default, unless a match needs the old one.
        proposed['compiler_ranges'] = [r for r in proposed.get('compiler_ranges', []) if profile(r) not in (old, target)]
        failures = {f['function'] for f in result['failures']}
        blocked = {f['vram'] for f in rows if f['name'] in failures}
        starts = sorted({f['vram'] for f in rows if f['vram'] is not None})
        for fn in rows:
            if fn['vram'] in blocked and profile(compiler_for(cfg, fn['vram'], fn)) == old:
                end = min([fn['vram'] + max(1, fn['size'])] + [v for v in starts if v > fn['vram']])
                replace_range(proposed, fn['vram'], end, old)
        # The normal guarded commit rechecks all affected matches and rescoring, then publishes atomically.
        net._apply_compiler_config(proj, proposed, log=log, preserve_matches=True)
        log(f"Default replaced. {len(failures)} functions retained their previous compiler settings.")
        return {'replaced':True, 'preserved':len(failures)}


def apply_matches(net, slug, selected, functions, log=print, safe_ranges=False, receipt=None):
    """Recheck selected trial successes and save through the ordinary compiler/match path."""
    net._rom_only(slug, 'applying compiler profile matches')
    if (not isinstance(functions, list) or not functions or len(functions) > 100000
            or not all(isinstance(name, str) for name in functions)):
        raise NetworkError('Run the non-match test and select its matching functions first.')
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    selected={'toolchain':selected['toolchain'],'flags':list(selected['flags'])}
    from .compiler_match_batch import apply
    return apply(net,slug,selected,list(dict.fromkeys(functions)),log,receipt)


def check_affected(net, slug, selected, functions, log=print):
    """Read-only check of affected matches with the selected profile, never inferred callers."""
    net._rom_only(slug, 'checking affected compiler-profile functions')
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])
            or not isinstance(functions, list) or not functions or len(functions) > 10000
            or not all(isinstance(name, str) for name in functions)):
        raise NetworkError('Choose affected functions and a compiler profile to check.')
    results = []
    names = list(dict.fromkeys(functions))
    for index, name in enumerate(names, 1):
        getattr(log, 'check_cancel', lambda:None)()
        try:
            proj = net._project(slug)
            fn = net._function(name, slug)
            source = fn['matched_source'] if fn['status'] == 'matched' else net._source_of(fn)
            if not source:
                raise NetworkError('No saved C is available.')
            ok, _, _ = net._judge_with(proj, fn, selected['toolchain'], selected['flags'], source)
            result = {'function':name, 'passed':ok, 'reason':'Matches this profile byte for byte.' if ok else 'Does not match this profile.'}
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            result = {'function':name, 'passed':False, 'reason':str(error)}
        results.append(result)
        getattr(log, 'progress', lambda value:None)({'done':index, 'total':len(names), 'phase':'Checking affected functions'})
        log(f"{index}/{len(names)}: {name}: {result['reason']}")
    return {'results':results, 'passed':sum(r['passed'] for r in results), 'total':len(results)}
