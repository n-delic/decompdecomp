"""Read-only trial of an alternative profile against matches using the project default."""
import copy
import json
from concurrent.futures import as_completed

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for
from .compiler_cleanup import profile, replace_range


def run(net, slug, selected, log=print, nonmatches=False):
    net._rom_only(slug, 'testing a compiler profile as default')
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    proj = net._project(slug)
    cfg = json.loads(proj['config'])
    default, target = profile(cfg['compiler']), profile(selected)
    rows = net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id', proj['id'])
    if target == default or not any(profile(compiler_for(cfg, f['vram'], f)) == target for f in rows):
        raise NetworkError('Choose a non-default compiler profile currently used in this project.')
    if toolchains.installed(target[0]) is None:
        raise NetworkError('Install the selected compiler before testing it as default.')
    def population(items, config):
        return [f for f in items if f['status'] == 'open'] if nonmatches else [f for f in items if f['status'] == 'matched' and profile(compiler_for(config, f['vram'], f)) == default]
    candidates = population(rows, cfg)
    def signature(items):
        return [tuple(f[k] for k in ('id','name','vram','unit','size','target','relocs','meta','status','matched_source','best_source','best_score')) for f in items]
    def headers():
        return [tuple(r) for r in net.store.all('SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version', proj['id'])]
    original_headers = headers()
    state = {'total':len(candidates), 'checked':0, 'passed':0, 'failed':0,
             'profile':{'toolchain':target[0], 'flags':list(target[1])},
             'default':cfg['compiler'], 'failures':[], 'matches':[], 'stale':False, 'nonmatches':nonmatches}
    report = getattr(log, 'progress', lambda value:None)
    cancel = getattr(log, 'check_cancel', lambda:None)
    def publish():
        report({**state, 'failures':list(state['failures']), 'matches':list(state['matches']), 'phase':'Testing non-matches' if nonmatches else 'Testing default-profile matches'})
    publish()
    log(f"Testing {len(candidates)} {'non-matching functions' if nonmatches else 'matched functions using the default'} against {target[0]} {' '.join(target[1])}. No settings will change.")
    def check(fn):
        cancel()
        source = net._source_of(fn) if nonmatches else fn['matched_source']
        if not source:
            return fn, False, 'No saved C is available.'
        try:
            ok, diff, _ = net._judge_with(proj, fn, target[0], list(target[1]), source)
            reason = '' if ok else 'Does not compile or compare.' if diff is None else f'Different bytes (score {diff.score}).'
            return fn, ok, reason
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            return fn, False, str(error)[:300]
    pending = [net._pool.submit(check, fn) for fn in candidates]
    try:
        for future in as_completed(pending):
            cancel()
            fn, ok, reason = future.result()
            state['checked'] += 1
            state['passed' if ok else 'failed'] += 1
            if ok:
                state['matches'].append(fn['name'])
            if not ok:
                state['failures'].append({'function':fn['name'], 'reason':reason})
            publish()
            if state['checked'] % 25 == 0 or state['checked'] == state['total']:
                log(f"{state['checked']}/{state['total']} checked: {state['passed']} passed, {state['failed']} did not pass.")
    finally:
        for future in pending:
            future.cancel()
    cancel()
    current = net.store.one('SELECT config FROM projects WHERE id=?', proj['id'])
    current_cfg = json.loads(current['config']) if current else None
    latest = net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id', proj['id'])
    latest_candidates = population(latest, current_cfg) if current_cfg else []
    state['stale'] = (not current_cfg or any(current_cfg.get(k) != cfg.get(k) for k in ('compiler','compiler_ranges'))
                      or headers() != original_headers or signature(latest_candidates) != signature(candidates))
    state['compatible'] = bool(candidates) and state['failed'] == 0 and not state['stale']
    log('Test finished. Settings, matches and saved scores are unchanged.' + (' Inputs changed during the test; rerun before making a decision.' if state['stale'] else ''))
    return state


def replace_default(net, slug, selected, log=print):
    """Promote a profile, retaining the previous settings wherever a saved match needs them."""
    with net.progress.judging(slug):
        proj = net._project(slug)
        cfg = json.loads(proj['config'])
        rows = net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id', proj['id'])
        result = run(net, slug, selected, log)
        if result['stale']:
            raise NetworkError('Code or settings changed during testing. Run the replacement again.')
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
        net._apply_compiler_config(proj, proposed, log=log)
        log(f"Default replaced. {len(failures)} functions retained their previous compiler settings.")
        return {'replaced':True, 'preserved':len(failures)}


def apply_matches(net, slug, selected, functions, log=print, safe_ranges=False):
    """Recheck selected trial successes and save through the ordinary compiler/match path."""
    net._rom_only(slug, 'applying compiler profile matches')
    if (not isinstance(functions, list) or not functions or len(functions) > 100000
            or not all(isinstance(name, str) for name in functions)):
        raise NetworkError('Run the non-match test and select its matching functions first.')
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    names = list(dict.fromkeys(functions))
    state = {'total':len(names), 'checked':0, 'passed':0, 'failed':0, 'matches':[], 'failures':[], 'applied':True}
    report = getattr(log, 'progress', lambda value:None)
    cancel = getattr(log, 'check_cancel', lambda:None)
    for name in names:
        cancel()
        reason = None
        try:
            proj = net._project(slug)
            fn = net._function(name, slug)
            if fn['status'] != 'open':
                raise NetworkError('No longer unmatched; skipped.')
            if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
                raise NetworkError('An agent is working on this function; retry after it releases the claim.')
            if net.store.one('SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND vram=?', proj['id'], fn['vram'])['n'] != 1:
                raise NetworkError('Shares an overlay address; needs an individual compiler review.')
            source = net._comparison_source(fn)
            if not source:
                raise NetworkError('No saved C is available.')
            ok, _, _ = net._judge_with(proj, fn, selected['toolchain'], selected['flags'], source)
            if not ok:
                raise NetworkError('Saved C no longer matches this profile; rerun the test.')
            cancel()
            result = net.apply_function_compiler(slug, name, selected['toolchain'], selected['flags'],
                                                 **({'preserve_other_profiles':True} if safe_ranges else {}))
            if not result['newly_matched']:
                raise NetworkError('Settings applied, but final verification did not save a new match. Review this function.')
            state['matches'].append(name)
            state['passed'] += 1
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            reason = str(error)
            state['failures'].append({'function':name, 'reason':reason,
                                      'blockers':getattr(error, 'compiler_blockers', []),
                                      'category':'affected_matches' if getattr(error, 'compiler_blockers', []) else
                                      'busy' if 'agent is working' in reason else
                                      'overlay' if 'overlay address' in reason else
                                      'no_source' if 'No saved C' in reason else
                                      'changed' if 'No longer unmatched' in reason or 'no longer matches' in reason else 'verification'})
            state['failed'] += 1
        state['checked'] += 1
        report({**state, 'phase':'Applying verified matches'})
        log(f"{state['checked']}/{state['total']}: {name}: {reason or 'verified match saved'}")
    return state


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
