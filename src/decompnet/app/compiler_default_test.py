"""Read-only trial of an alternative profile against matches using the project default."""
import json
from concurrent.futures import as_completed

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for
from .compiler_cleanup import profile


def run(net, slug, selected, log=print):
    net._rom_only(slug, 'testing a compiler profile as default')
    if (not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str)
            or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags'])):
        raise NetworkError('Choose a compiler profile from the current list.')
    proj = net._project(slug)
    cfg = json.loads(proj['config'])
    default, target = profile(cfg['compiler']), profile(selected)
    rows = net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id', proj['id'])
    if target == default or not any(profile(compiler_for(cfg, f['vram'])) == target for f in rows):
        raise NetworkError('Choose a non-default compiler profile currently used in this project.')
    if toolchains.installed(target[0]) is None:
        raise NetworkError('Install the selected compiler before testing it as default.')
    candidates = [f for f in rows if f['status'] == 'matched' and profile(compiler_for(cfg, f['vram'])) == default]
    def signature(items):
        return [tuple(f[k] for k in ('id','name','vram','unit','size','target','relocs','meta','status','matched_source')) for f in items]
    def headers():
        return [tuple(r) for r in net.store.all('SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version', proj['id'])]
    original_headers = headers()
    state = {'total':len(candidates), 'checked':0, 'passed':0, 'failed':0,
             'profile':{'toolchain':target[0], 'flags':list(target[1])},
             'default':cfg['compiler'], 'failures':[], 'stale':False}
    report = getattr(log, 'progress', lambda value:None)
    cancel = getattr(log, 'check_cancel', lambda:None)
    def publish():
        report({**state, 'failures':list(state['failures']), 'phase':'Testing default-profile matches'})
    publish()
    log(f"Testing {len(candidates)} matched functions using the default against {target[0]} {' '.join(target[1])}. No settings will change.")
    def check(fn):
        cancel()
        if not fn['matched_source']:
            return fn, False, 'No saved matching C is available.'
        try:
            ok, diff, _ = net._judge_with(proj, fn, target[0], list(target[1]), fn['matched_source'])
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
    latest_candidates = [f for f in latest if current_cfg and f['status'] == 'matched' and profile(compiler_for(current_cfg, f['vram'])) == default]
    state['stale'] = (not current_cfg or any(current_cfg.get(k) != cfg.get(k) for k in ('compiler','compiler_ranges'))
                      or headers() != original_headers or signature(latest_candidates) != signature(candidates))
    state['compatible'] = bool(candidates) and state['failed'] == 0 and not state['stale']
    log('Test finished. Settings, matches and saved scores are unchanged.' + (' Inputs changed during the test; rerun before making a decision.' if state['stale'] else ''))
    return state
