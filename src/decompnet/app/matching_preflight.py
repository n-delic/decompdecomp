"""Bounded current-toolchain flag checks before a long ROM search, cached by actual judge inputs."""
import hashlib
import json
import threading

from .. import compiler, toolchains
from ..network import NetworkError
from ..project import compiler_for
from . import switch_tables
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
    from .profile_hypotheses import captured, kmc_priorities, kmc_sqrt_probe, neighbours, prioritized
    from .repair_ranking import evidence, instruction_rows
    choices = [list(own['flags'])]
    # Debug labels can change old GCC assembler scheduling. A near match deserves
    # these cheap same-toolchain checks before broad optimization exploration.
    count = dict(fn).get('n_insns') or 0
    promising = count > 0 and fn['best_score'] is not None and fn['best_score'] <= count * 10
    debug = net.debug_flag_variants(own['toolchain'], list(own['flags'])) if promising and hasattr(net, 'debug_flag_variants') else []
    focused = []
    if own['toolchain'] == 'gcc-2.7.2-kmc':
        rows = instruction_rows(evidence(net, fn['id'], None))
        assembly = dict(fn).get('asm', '')
        focused = [list(flags) for flags in kmc_priorities(own, assembly, rows)]
        if sqrt_probe := kmc_sqrt_probe(own, assembly, rows):
            focused.insert(0, sqrt_probe)
        if promising and '-O1' in own['flags']:
            from .profile_hypotheses import kmc_variants
            paired_schedule = next((flags for flags in kmc_variants(own['toolchain'], own['flags'])
                                    if {'-fschedule-insns', '-fschedule-insns2'} <= set(flags)), None)
            if paired_schedule and paired_schedule not in focused:
                focused.append(paired_schedule)
    for flags in focused+captured(net,proj,own)+debug+prioritized(proj, fn, own)+neighbours(net, proj, fn, own):
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
    from decomprepair import guarded_loop_repairs, halfword_store_scheduling, strided_table_loop
    context = net._context(proj['id'],fn['unit']) if hasattr(net,'_context') else None
    from .profile_hypotheses import float_sqrt_source
    sqrt_source = float_sqrt_source(own, dict(fn).get('asm') or '', fn['best_source'], fn['name'],
                                    context['header'] if context else '')
    if sqrt_source:
        probes.insert(1, (list(own['flags']), sqrt_source))
        if '-ffast-math' not in own['flags']:
            probes.insert(2, ([*own['flags'], '-ffast-math'], sqrt_source))
    paired = guarded_loop_repairs.probes(proj,fn,own,context['header'] if context else '')
    # A poll loop the target compiled without strength reduction: the profile alone ties while the source indexes
    # by element, so it is probed joined with the byte-offset, record-field and one-step spellings.
    rename = getattr(net, 'rename_tokens', None)  # the source names symbols as the project renamed them
    named = dict(fn) | {'asm': rename(proj, dict(fn).get('asm') or '') if rename else dict(fn).get('asm') or ''}
    paired = [*paired, *strided_table_loop.probes(proj, named, own, context['header'] if context else '')]
    # Preserve this tiny joint frontier independently of isolated source scores.
    probes[1:1] = [(list(flags),source) for flags,source in paired]
    if (own['toolchain'] == 'gcc-2.7.2-kmc' and '-fno-strength-reduce' in own['flags']):
        restored = [flags for flags in focused if '-fno-strength-reduce' not in flags]
        if restored:
            from decomprepair import pointer_walk_index_repairs
            indexed = pointer_walk_index_repairs.variants(fn['best_source'], fn['name'])
            if indexed:
                probes.insert(2, (restored[0], indexed[0][1]))
    if own['toolchain'] == 'gcc-2.7.2-kmc' and '-fno-strength-reduce' in own['flags']:
        # m2c's stepped induction variables written as the counter's multiples, judged with strength reduction back on
        # (one function went 244 -> exact: neither the source nor the profile alone changes the score).
        from decomprepair import stepped_induction_index
        probes[1:1] = [(list(flags), source) for flags, source in stepped_induction_index.probes(dict(proj), dict(fn), own)]
    # Keep the complete load/store hypothesis ahead of isolated scheduling probes.
    halfword = [(list(flags), source) for flags, source in halfword_store_scheduling.probes(dict(proj), dict(fn), own)]
    probes[1:1] = halfword
    probes = list(dict.fromkeys((tuple(flags),source) for flags,source in probes))
    probes = [(list(flags),source) for flags,source in probes]
    candidates = [(own['toolchain'], tuple(flags)) for flags, _ in probes]
    input_digest = net._comparison_digest(proj, fn, fn['best_source'], candidates)
    from .baseline_status import revision
    key = 'matching-preflight-v9:'+revision()+':'+input_digest+':'+hashlib.sha256(repr(probes).encode()).hexdigest()
    result = {'tested': 0, 'best': None, 'pending': 0}
    best_key = None
    relocation_source = None
    allowance = min(MAX_PROFILES, max_profiles)
    jump_probes = []
    def ensure():
        if check() is False:
            raise PreflightStopped()
    try:
        with compiler.cancellable(ensure):
            for index, (flags, source) in enumerate(probes):
                probe_key = key+':probe:'+hashlib.sha256(repr((flags,source)).encode()).hexdigest()
                old = net.project_store(proj['id']).one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], probe_key)
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
                    if index == 0 and diff is not None and not exact:
                        item['jump_profiles'] = switch_tables.relocation_profiles(net, proj, fn, own, problems)
                        if item['jump_profiles']:
                            item['jump_sources'] = [candidate for _, candidate in switch_tables.variants(
                                source, fn['name'], switch_tables.read(net, proj, fn))]
                    if (flags, source) in jump_probes:
                        item['clean_link'] = not problems
                    if index == 0 and not exact and own['toolchain'] == 'gcc-2.7.2-kmc':
                        rows = instruction_rows(getattr(diff, 'rows', []))
                        inferred = [list(flags) for flags in kmc_priorities(own, assembly, rows)]
                        if sqrt := kmc_sqrt_probe(own, assembly, rows):
                            inferred.insert(0, sqrt)
                            item['sqrt_probe'] = sqrt
                        item['inferred'] = inferred
                    if index == 0 and item['link'] and dict(proj).get('arch') == 'mips':
                        from decomprepair.aligned_relocation_names import replacement
                        item['relocation_source'] = replacement(source, getattr(diff, 'rows', ()),
                                                              item['link']['symbols'],
                                                              context['header'] if context else '')
                    if item['link']:
                        from .helper_call_diagnostics import suggestions
                        for hint in suggestions(getattr(diff, 'rows', [])):log(hint)
                    with net.project_store(proj['id']).tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                                   (fn['id'],probe_key,'profile','completed bounded profile',json.dumps(item)))
                if index == 0 and item.get('inferred'):
                    # Keep these trials when resuming a small budget, even if no live diff survives.
                    inferred = [(variant, source) for variant in item['inferred']]
                    probes[1:] = halfword + inferred + [probe for probe in probes[1:]
                                                        if probe not in halfword and probe not in inferred]
                if index == 0 and item.get('jump_profiles'):
                    jump_probes = [(flags, source) for flags in item['jump_profiles']]
                    for child in item.get('jump_sources', ()):
                        jump_probes.extend((flags, child) for flags in [list(own['flags']), *item['jump_profiles']])
                    probes[1:] = jump_probes + [probe for probe in probes[1:] if probe not in jump_probes]
                if (flags, source) in jump_probes and item.get('clean_link') and item['score'] is not None:
                    old = result.get('jump_table_profile')
                    if old is None or item['score'] < old['score']:
                        result['jump_table_profile'] = item
                if index == 0 and item.get('relocation_source'):
                    relocation_source = item['relocation_source']
                    probes.insert(1, (list(own['flags']), relocation_source))
                    result['link'] = item['link']
                    continue
                if source == relocation_source:
                    if not item.get('link') and item['score'] is not None:
                        result['relinked'] = {'source': source, 'baseline': fn['best_source'], 'digest': input_digest,
                                              'candidates': candidates}
                        result.pop('link', None)
                        result['best'] = item
                    break
                if item.get('link'):
                    result['link'] = item['link']
                    if (set(item['link']['symbols']) <= {'sqrt', 'sqrtf'} and
                            (index == 0 and (item.get('sqrt_probe') or sqrt_source)
                             or source == sqrt_source and '-ffast-math' not in flags)):
                        # This probe may remove the compiler-generated reference itself.
                        continue
                    break
                if item['score'] is not None and (result['best'] is None or item['score'] < result['best']['score']):
                    result['best'] = item
                    best_key = probe_key
                if item['exact']:
                    result.pop('link', None)
                    result['pending'] = 0
                    break
    except PreflightStopped:
        return result
    if check() is False:
        return result
    result['cached'] = result['tested'] == 0
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'],key,'preflight','resumable compiler preflight',json.dumps(result)))
    if result['pending']:
        log(f"Compiler preflight has {result['pending']} untested profiles remaining for this saved source/context; later passes resume them.")
    if relocated := result.get('jump_table_profile'):
        log('Jump-table relocation cleared with ' + ' '.join(relocated['flags']) +
            f" (linked score {relocated['score']:,}).")
    if result.get('link'):
        log(result['link']['message'])
    best = result.get('best')
    if (best and best['exact'] and best['flags'] == own['flags']
            and proj['slug'] not in getattr(net, 'links', {})):
        result['applied'] = recover_exact(net, proj, fn, input_digest, candidates, best['source'])
        if result['applied']:
            log('Rejudged C matched with the current settings: '+fn['name'])
    if best and not result.get('relinked') and not result.get('applied') and (best['flags'] != own['flags'] or best.get('source', fn['best_source']) != fn['best_source']) and best['score'] < (fn['best_score'] if fn['best_score'] is not None else float('inf')):
        fresh = net.project_row(proj['slug'])
        current = net._function(fn['name'], proj['slug'])
        # Another worker may have improved the C during probing. Never apply an old-source hypothesis.
        if (current['status'] == 'open' and current['best_source'] == fn['best_source']
                and net._comparison_digest(fresh,current,current['best_source'],candidates)==input_digest):
            review_only = '-ffast-math' in best['flags'] and '-ffast-math' not in own['flags']
            result['review_only'] = review_only and best['exact']
            if (best['exact'] and not review_only and net.auto_adjusts(fresh)
                    and best.get('source', fn['best_source']) == fn['best_source']):
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
                        options = {'review_only': True} if review_only else {}
                        said = net.suggest(agent, proj['slug'], 'Compiler preflight found a rejudged improvement.',
                                    compiler=best['toolchain'], flags=' '.join(best['flags']), function=fn['name'],
                                    ranges=f"0x{fn['vram']:X}-0x{fn['vram']+fn['size']:X}", source=best.get('source', fn['best_source']), **options)
                    except NetworkError as error:
                        # A partial/stale hypothesis may fail the exact suggestion gate. It must not
                        # abort source search or apply settings without the normal revalidation.
                        if not str(error).startswith('not suggested:'):raise
                        said = str(error)
                        result['suggestion_rejected'] = said
                        log('Compiler preflight kept the working settings and will continue source search: '+str(error))
                        if not best['exact'] and (note := closer(net, agent, proj['slug'], fn['name'], best,
                                                                  fn['best_score'], best['score'])):
                            log(note)
                    if best['exact']:
                        result['applied'] = net._function(fn['name'],proj['slug'])['status']=='matched'
                        if not result['applied']:log(said)
                    if best_key:
                        best['notified']=review_only or not best['exact'] or bool(result.get('applied')) or not net.auto_adjusts(fresh)
                        with net.project_store(proj['id']).tx() as db:
                            db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',
                                       (json.dumps(best),fn['id'],best_key))
                log('Compiler preflight found a better source/profile hypothesis: '+' '.join(best['flags'])+
                    '. Partial results need review; exact results also respect automatic compiler settings.')
    return result


def recover_exact(net, proj, fn, digest, candidates, candidate=None):
    """Confirm a saved winner through the normal judge using the current project inputs."""
    slug = proj['slug']
    fresh = net.project_row(slug)
    current = net._function(fn['name'], slug)
    source = fn['best_source']
    if (current['status'] != 'open' or current['best_source'] != source or
            net._comparison_digest(fresh, current, source, candidates) != digest):
        return False
    agent, _ = net.local_agent('autopilot')
    try:
        net.try_quietly(agent, fresh, current, candidate or source, synchronize=False, exact_only=True)
    except NetworkError:
        return False  # Claim, boundary or input state changed during revalidation.
    return net._function(fn['name'], slug)['status'] == 'matched'


CLOSER = 2  # a profile that scores the saved C at most 1/CLOSER of its own settings' score is worth telling the user


def closer(net, agent, slug, name, profile, before, after):
    """Tell the user, in words on the project page, that `name` comes much closer under another profile though not
    exactly: the exact-only gate (suggest refuses a compiler its C doesn't match with) kept that silent, and a
    library strcmp went from 130 to 10 under -fno-schedule-insns2 with nobody hearing of it. A words-only suggestion
    applies nothing; the same note again is the same suggestion. None when it isn't much closer."""
    from ..network import NetworkError
    if before is None or after is None or after <= 0 or after * CLOSER > before:
        return None
    settings = profile['toolchain'] + ' ' + ' '.join(profile['flags'])
    lead = f'{name} comes closer under {settings}:'
    if any(item.get('status') == 'open' and str(item.get('text', '')).startswith(lead)
           for item in net.cfg(net.project_row(slug)).get('suggestions', [])):
        return None  # (already on the project page: each pass would add it again)
    try:
        said = net.suggest(agent, slug, f'{lead} {before} → {after}, not exact yet. '
                                        'Its C still needs work under those settings before they can apply to it.')
    except NetworkError as error:
        return f'Closer profile not noted: {error}'
    return f'{name} closer under {settings}: {before} → {after}. {said}'

