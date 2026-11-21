"""Test saved unmatched C with a selected compiler, then revalidate every applied match."""
import copy
import json
from collections import Counter
from concurrent.futures import as_completed

from .. import toolchains
from ..network import NetworkError
from ..project import compiler_for
from .compiler_cleanup import commit_config, profile, replace_range, restore_config, saved_attempts


def selected_profile(selected):
    if not isinstance(selected, dict) or not isinstance(selected.get('toolchain'), str) or not isinstance(selected.get('flags'), list) or not all(isinstance(f, str) for f in selected['flags']):
        raise NetworkError('Choose a compiler profile from the current list.')
    if toolchains.installed(selected['toolchain']) is None:
        raise NetworkError('Install the selected compiler before testing this profile.')
    return {'toolchain': selected['toolchain'], 'flags': list(selected['flags'])}


def run(net, slug, selected, log=print):
    net._rom_only(slug, 'testing unmatched functions with a compiler profile')
    selected = selected_profile(selected)
    proj = net._project(slug)
    cfg = net.cfg(proj)
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY id", proj['id'])
    if not any(profile(compiler_for(cfg, f['vram'])) == profile(selected) for f in net.store.all('SELECT vram FROM functions WHERE project_id=?', proj['id'])):
        raise NetworkError('Choose a compiler profile currently used in this project.')
    report = getattr(log, 'progress', lambda value: None)
    cancel = getattr(log, 'check_cancel', lambda: None)
    state = {'total': len(rows), 'checked': 0, 'passed': 0, 'failed': 0, 'failures': [], 'matches': [], 'nonmatches': True, 'stale': False}
    headers = [tuple(r) for r in net.store.all('SELECT * FROM context_versions WHERE project_id=?', proj['id'])]
    def check(fn):
        cancel()
        source = net._source_of(fn)
        if not source:
            return fn['name'], False, 'No saved C is available.', 'no_source'
        try:
            ok, _, _ = net._judge_with(proj, fn, selected['toolchain'], selected['flags'], source)
            return fn['name'], ok, '' if ok else 'Different compiled bytes.', 'verification'
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            return fn['name'], False, str(error), 'verification'
    report(dict(state, phase='Testing non-matches'))
    for offset in range(0, len(rows), 32):
        pending = [net._pool.submit(check, item) for item in rows[offset:offset + 32]]
        try:
            for future in as_completed(pending):
                cancel()
                name, ok, reason, category = future.result()
                state['checked'] += 1
                state['passed' if ok else 'failed'] += 1
                state['function'] = name
                if ok:
                    state['matches'].append(name)
                else:
                    state['failures'].append({'function': name, 'reason': reason, 'category': category})
                report({**state, 'matches': list(state['matches']), 'failures': list(state['failures']), 'phase': 'Testing non-matches'})
        finally:
            for future in pending:
                future.cancel()
    current = net.store.one('SELECT config FROM projects WHERE id=?', proj['id'])
    latest = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY id", proj['id'])
    state['stale'] = (not current or json.loads(current['config']) != cfg or [tuple(f) for f in latest] != [tuple(f) for f in rows]
                      or headers != [tuple(r) for r in net.store.all('SELECT * FROM context_versions WHERE project_id=?', proj['id'])])
    log(f"Checked {state['checked']} functions: {state['passed']} exact matches. No settings changed.")
    return state


def apply(net, slug, selected, functions, log=print):
    selected = selected_profile(selected)
    cancel = getattr(log, 'check_cancel', lambda: None)
    report = getattr(log, 'progress', lambda value: None)
    state = {'total': len(functions), 'checked': 0, 'passed': 0, 'failed': 0, 'failures': [], 'matches': [], 'applied': True, 'reused': 0}
    for name in functions:
        cancel()
        try:
            with net.progress.judging(slug):
                proj = net._project(slug)
                fn = net._function(name, slug)
                source = net._source_of(fn)
                if fn['status'] != 'open' or not source:
                    raise NetworkError('Inputs or status changed. Retest this function.')
                rows = net.store.all('SELECT name,vram,size FROM functions WHERE project_id=? AND vram IS NOT NULL', proj['id'])
                addresses = Counter(f['vram'] for f in rows)
                if fn['vram'] is None or addresses[fn['vram']] > 1 or not fn['size']:
                    raise NetworkError('Shared or unavailable address range; review this function separately.')
                if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
                    raise NetworkError('An agent is working on this function. Retry after it finishes.')
                if not net._judge_with(proj, fn, selected['toolchain'], selected['flags'], source)[0]:
                    raise NetworkError('The saved C no longer matches this compiler profile.')
                cfg = copy.deepcopy(net.cfg(proj))
                end = min([fn['vram'] + fn['size']] + [f['vram'] for f in rows if f['vram'] > fn['vram']])
                # This is an exact proof for this function only, not evidence that
                # adjacent code shares the compiler. Never widen the override.
                replace_range(cfg, fn['vram'], end, profile(selected))
                original = net.cfg(proj)
                attempts = saved_attempts(net, [fn['id']])
                commit_config(net, proj, cfg, log)
                try:
                    agent, _ = net.local_agent('autopilot')
                    fresh = net._function(name, slug)
                    result = net.try_quietly(agent, net._project(slug), fresh, source)
                    if not result.get('matched'):
                        raise NetworkError('The function changed during application. Retest before applying again.')
                except Exception:
                    # A successful range change must never outlive a failed match.
                    restore_config(net, net._project(slug), original, cfg, attempts)
                    raise
                state['passed'] += 1
                state['matches'].append(name)
        except (NetworkError, toolchains.ToolchainError, OSError) as error:
            state['failed'] += 1
            state['failures'].append({'function': name, 'reason': str(error)})
        state['checked'] += 1
        report({**state, 'matches': list(state['matches']), 'failures': list(state['failures']), 'phase': 'Applying verified matches'})
    return state
