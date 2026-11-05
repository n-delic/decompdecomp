"""Bounded current-toolchain flag checks before a long ROM search, cached by actual judge inputs."""
import hashlib
import json
import threading

from .. import compiler, toolchains
from ..network import NetworkError
from ..project import compiler_for
from .candidate_diagnostics import unresolved

MAX_PROFILES = 4


class PreflightStopped(Exception):
    pass


def run(net, proj, fn, log=lambda text: None, check=lambda: None, max_profiles=MAX_PROFILES):
    """Only one preflight per function; another worker can continue without duplicating probes."""
    locks = net.__dict__.setdefault('_matching_preflight_locks', {})
    lock = locks.setdefault(fn['id'], threading.Lock())
    if not lock.acquire(blocking=False):
        return {'tested': 0}
    try:
        return _run(net, proj, fn, log, check, max_profiles)
    finally:
        lock.release()


def _run(net, proj, fn, log, check, max_profiles):
    """Use installed profiles only; an applied winner is rejudged and scoped by the existing settings gate."""
    if max_profiles <= 0:
        return {'tested': 0}
    if (not hasattr(net, '_comparison_digest') or net.fork_slug(proj['slug']) or not net.is_rom(proj)
            or fn['status'] != 'open' or not fn['best_source']):
        return {'tested': 0}
    cfg = net.cfg(proj)
    own = compiler_for(cfg, fn['vram'], fn)
    if own.get('cmd') or not own.get('toolchain') or toolchains.installed(own['toolchain']) is None:
        return {'tested': 0}
    from .profile_hypotheses import captured, neighbours, prioritized
    choices = [list(own['flags'])]
    # Debug labels can change old GCC assembler scheduling. A near match deserves
    # these cheap same-toolchain checks before broad optimization exploration.
    count = dict(fn).get('n_insns') or 0
    promising = count > 0 and fn['best_score'] is not None and fn['best_score'] <= count * 10
    debug = net.debug_flag_variants(own['toolchain'], list(own['flags'])) if promising and hasattr(net, 'debug_flag_variants') else []
    for flags in captured(net,proj,own)+debug+prioritized(proj, fn, own)+neighbours(net, proj, fn, own):
        if flags not in choices:choices.append(flags)
    # Other candidates retain the existing optimization-first exploration order.
    for flags in net.flag_variants(own['toolchain'], list(own['flags'])):
        if list(flags) not in choices:
            choices.append(list(flags))
    probes = [(flags, fn['best_source']) for flags in choices]
    if dict(proj).get('arch') == 'mips' and own['toolchain'].startswith('ido-'):
        from ..arch.mips_runtime import candidate
        if source := candidate(dict(fn).get('asm', ''), fn['name']):
            ranked = prioritized(proj, fn, own)
            probes.insert(1, (ranked[0] if ranked else list(own['flags']), source))
    from . import guarded_loop_repairs
    context = net._context(proj['id'],fn['unit']) if hasattr(net,'_context') else None
    paired = guarded_loop_repairs.probes(proj,fn,own,context['header'] if context else '')
    # Preserve this tiny joint frontier independently of isolated source scores.
    probes[1:1] = [(list(flags),source) for flags,source in paired]
    probes = list(dict.fromkeys((tuple(flags),source) for flags,source in probes))
    probes = [(list(flags),source) for flags,source in probes]
    candidates = [(own['toolchain'], tuple(flags)) for flags, _ in probes]
    input_digest = net._comparison_digest(proj, fn, fn['best_source'], candidates)
    key = 'matching-preflight-v3:'+input_digest+':'+hashlib.sha256(repr(probes).encode()).hexdigest()
    result = {'tested': 0, 'best': None, 'pending': 0}
    best_key = None
    allowance = min(MAX_PROFILES, max_profiles)
    def ensure():
        if check() is False:
            raise PreflightStopped()
    try:
        with compiler.cancellable(ensure):
            for flags, source in probes:
                probe_key = key+':probe:'+hashlib.sha256(repr((flags,source)).encode()).hexdigest()
                old = net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], probe_key)
                if old:
                    item = json.loads(old['feedback'])
                else:
                    if result['tested'] >= allowance:
                        result['pending'] += 1
                        continue
                    ensure()
                    exact, diff, problems = net._judge_with(proj, fn, own['toolchain'], list(flags), source)
                    result['tested'] += 1
                    ensure()  # An interrupted in-flight profile remains retryable.
                    item = {'toolchain': own['toolchain'], 'flags': list(flags), 'source': source,
                            'score': None if diff is None else 0 if exact else max(1,diff.score),
                            'exact': bool(exact), 'link': unresolved([problems]), 'notified': False}
                    if item['link']:
                        from .helper_call_diagnostics import suggestions
                        for hint in suggestions(getattr(diff, 'rows', [])):log(hint)
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                   (fn['id'],probe_key,'profile','completed bounded profile',json.dumps(item)))
                if item.get('link'):
                    result['link'] = item['link']
                    break
                if item['score'] is not None and (result['best'] is None or item['score'] < result['best']['score']):
                    result['best'] = item
                    best_key = probe_key
                if item['exact']:
                    result['pending'] = 0
                    break
    except PreflightStopped:
        return result
    if check() is False:
        return result
    result['cached'] = result['tested'] == 0
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'],key,'preflight','resumable compiler preflight',json.dumps(result)))
    if result['pending']:
        log(f"Compiler preflight has {result['pending']} untested profiles remaining for this saved source/context; later passes resume them.")
    if result.get('link'):
        log(result['link']['message'])
    best = result.get('best')
    if best and (best['flags'] != own['flags'] or best.get('source', fn['best_source']) != fn['best_source']) and best['score'] < (fn['best_score'] if fn['best_score'] is not None else float('inf')):
        fresh = net.project_row(proj['slug'])
        current = net._function(fn['name'], proj['slug'])
        # Another worker may have improved the C during probing. Never apply an old-source hypothesis.
        if (current['status'] == 'open' and current['best_source'] == fn['best_source']
                and net._comparison_digest(fresh,current,current['best_source'],candidates)==input_digest):
            if best['exact'] and net.auto_adjusts(fresh) and best.get('source', fn['best_source']) == fn['best_source']:
                try:
                    net.apply_function_compiler(proj['slug'], fn['name'], best['toolchain'], best['flags'])
                    result['applied'] = True
                    log('Compiler preflight applied a reverified improvement to '+fn['name']+' only.')
                except NetworkError as error:
                    log('Compiler preflight left settings unchanged: '+str(error))
            else:
                if not best.get('notified'):
                    agent, _ = net.local_agent('autopilot')
                    try:
                        said = net.suggest(agent, proj['slug'], 'Compiler preflight found a rejudged improvement.',
                                    compiler=best['toolchain'], flags=' '.join(best['flags']), function=fn['name'],
                                    ranges=f"0x{fn['vram']:X}-0x{fn['vram']+fn['size']:X}", source=best.get('source', fn['best_source']))
                    except NetworkError as error:
                        # A partial/stale hypothesis may fail the exact suggestion gate. It must not
                        # abort source search or apply settings without the normal revalidation.
                        if not str(error).startswith('not suggested:'):raise
                        said = str(error)
                        result['suggestion_rejected'] = said
                        log('Compiler preflight kept the working settings and will continue source search: '+str(error))
                    if best['exact']:
                        result['applied'] = net._function(fn['name'],proj['slug'])['status']=='matched'
                        if not result['applied']:log(said)
                    if best_key:
                        best['notified']=not best['exact'] or bool(result.get('applied')) or not net.auto_adjusts(fresh)
                        with net.store.tx() as db:
                            db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',
                                       (json.dumps(best),fn['id'],best_key))
                log('Compiler preflight found a better source/profile hypothesis: '+' '.join(best['flags'])+
                    '. Partial results need review; exact results also respect automatic compiler settings.')
    return result
