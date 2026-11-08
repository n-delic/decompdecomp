"""Bounded build-scoped search notes for agents: useful differences without full assembly replay."""
import hashlib

from . import (
    agent_tokens,
    allocation_evidence,
    experiment_evidence,
    frame_plateau,
    permuter_focus,
    region_repairs,
    repair_ranking,
)

LIMIT = 64


def remember(net, agent, proj, fn, source, score, *, output=None):
    if score is None:
        return
    cache = net.__dict__.setdefault('_search_evidence',{})
    current = net.project_store(fn['project_id']).one('SELECT * FROM functions WHERE id=?',fn['id']) or fn
    key = (fn['id'],experiment_evidence.build_scope(net,proj,current))
    pool = cache.setdefault(key,{})
    rows = permuter_focus.normalized(repair_ranking.evidence(net,fn['id'],agent.id))
    from ..repair_feedback import current_trial
    parent_hash = (current_trial() or {}).get('base_source_sha256')
    parent = pool.get(parent_hash)
    regional = region_repairs.progress(parent['rows'],rows,proj['arch']) if parent else ''
    pool[hashlib.sha256(source.encode()).hexdigest()] = {'score':score,'rows':rows,'frame_source':source if frame_plateau.gap(rows,proj['arch']) and len(source)<=65536 else None,'kind':permuter_focus.classify(rows),'allocation':allocation_evidence.diagnose(rows,proj['arch']),'regional_progress':regional,'output':output,'family':(current_trial() or {}).get('family','manual')}
    while len(pool)>8:
        frames=[key for key,value in pool.items() if frame_plateau.gap(value['rows'],proj['arch'])]
        protected=min(frames,key=lambda key:(frame_plateau.gap(pool[key]['rows'],proj['arch']),pool[key]['score'])) if frames else None
        best_hash=hashlib.sha256((current['best_source'] or '').encode()).hexdigest()
        oldest=next(key for key in pool if key not in {protected,best_hash})
        del pool[oldest]
    while len(cache)>LIMIT:
        del cache[next(iter(cache))]


def brief(net, proj, fn):
    """Only current-input evidence is shown. Retained alternatives are hints, not saved winners."""
    source = fn['best_source'] or ''
    key = (fn['id'],experiment_evidence.build_scope(net,proj,fn))
    pool = getattr(net,'_search_evidence',{}).get(key,{})
    current = pool.get(hashlib.sha256(source.encode()).hexdigest())
    lines = []
    scratch_hint = ''
    # Scores alone cannot identify repeated machine code. Use only measured output fingerprints.
    groups = {}
    for source_hash, trial in list(pool.items()):
        if trial.get('output'):
            groups.setdefault(trial['output'], []).append((source_hash, trial))
    repeated = [trials for trials in groups.values() if len(trials) > 1]
    if repeated:
        trials = max(repeated, key=len)
        families = list(dict.fromkeys(t['family'] for _, t in trials))[:3]
        lines.append(f"Repeated compiler output: {len(trials)} source variants ({', '.join(families)}) "
                     "produced the same measured function fingerprint. Try a different repair shape; "
                     "this does not prove those families exhausted or validate linked data.")
    if current and source and permuter_focus.scheduling_only(current['rows']):
        from .call_preparation_repairs import explanation
        header = (net._context(proj['id'],fn['unit']) or {'header':''})['header']
        if clue := explanation(source,fn['name'],header,proj['arch']):lines.append(clue)
    if current and permuter_focus.scheduling_only(current['rows']):
        lines.append('Only instruction order differs. Revisit earlier one-pass scopes and try bounded '
                     'argument-preparation/scheduling repairs before broad type or ABI changes.')
    if current:
        rows = [r for r in current['rows'] if len(r)==3 and r[0]!=' ']
        from ..diff_summary import advice
        if hint := advice(current['rows']):lines.append(hint)
        clues = allocation_evidence.text(current['rows'],proj['arch'])
        if clues:lines.append(clues)
        if clue:=frame_plateau.brief(source,fn['name'],(net._context(proj['id'],fn['unit']) or {'header':''})['header'],current['rows'],proj['arch']):lines.append(clue)
        if allocation_evidence.diagnose(current['rows'],proj['arch'])['register_only']:
            from .copy_lifetime_repairs import hint
            from .scratch_role_repairs import brief as scratch_brief
            scratch_hint = scratch_brief(source,fn['name'],(net._context(proj['id'],fn['unit']) or {'header':''})['header'],proj['arch'],current['rows'])
            if scratch_hint:lines.append(scratch_hint)
            else:
                from . import scratch_role_repairs
                if scratch_role_repairs.POINTER.search(source):
                    lines.append(scratch_role_repairs.explanation(source,fn['name'],(net._context(proj['id'],fn['unit']) or {'header':''})['header'],proj['arch']))
            if clue := hint(source,fn['name']):lines.append(clue)
        from . import allocation_search
        if clue := allocation_search.brief(net,proj,fn,source,current['rows']):lines.append(clue)
        if region := region_repairs.summary(current['rows'],proj['arch']):lines.append(region)
        if current.get('regional_progress'):lines.append(current['regional_progress'])
        plan=allocation_evidence.plan(current['rows'],proj['arch'])
        from .copy_lifetime_repairs import FAMILIES, variants
        applicable = {family for family, _ in variants(source,fn['name'])}
        from . import parameter_carriers
        has_carriers=bool(parameter_carriers.variants(source,fn['name'],(net._context(proj['id'],fn['unit']) or {'header':''})['header'],proj['arch']))
        families = [family for family in plan['families'] if (family not in FAMILIES or family in applicable)
                    and (family != 'scratch_role_reuse' or scratch_hint)
                    and (family != 'parameter_carriers' or has_carriers)]
        next_family=(' Try '+families[0]+'.') if families else ''
        lines.append(f"Remaining mismatch: {plan['category'] or 'mixed'}; {len(rows)} displayed differences.{next_family}")
        lines.extend(f"  {r[0]} {str(r[1])[:80]} => {str(r[2])[:80]}" for r in rows[:4])
        from .repair_sequences import starting
        learned = starting(net,proj,fn,current['rows'])
        from . import repair_sequence_metrics, repair_sequences
        if measured := repair_sequence_metrics.brief(net,repair_sequences.profile(net,proj,fn),repair_sequences.focus(current['rows'],proj['arch'])):lines.append(measured)
        if learned:
            lines.append('Previously exact repair sequences start with: '+', '.join(learned[:2])+'. Use local variants when applicable; this is advisory.')
        alternatives = [v for v in pool.values() if v is not current and v['score']>0 and (v['score']<=max(1,current['score'])*1.25 or (current['score']>0 and frame_plateau.gap(v['rows'],proj['arch'])))]
        frames=sorted((v for v in alternatives if frame_plateau.gap(v['rows'],proj['arch'])),
                      key=lambda v:(frame_plateau.gap(v['rows'],proj['arch']),v['score']))
        if frames and frames[0].get('frame_source'):
            alternative=frames[0]
            alternative_source=alternative['frame_source']
            lines.append('Historical root SHA256 '+hashlib.sha256(alternative_source.encode()).hexdigest()[:20]+': '+frame_plateau.brief(alternative_source,fn['name'],(net._context(proj['id'],fn['unit']) or {'header':''})['header'],alternative['rows'],proj['arch']))
        if frames:
            lines.append('Retained body-correct alternative: score '+str(frames[0]['score'])+'; frame '+str(frame_plateau.gap(frames[0]['rows'],proj['arch']))+' bytes short. Inspect its historical source and try halfword_store_normalization; lower numeric score alone is not a better repair root.')
        if alternatives:
            lines.append('Recent tested alternatives (not adopted): '+', '.join(f"{v['kind'] or 'mixed'} score {v['score']} (frame {v.get('allocation',{}).get('candidate_frame','unknown')})" for v in alternatives[-2:]))
    if current is None and getattr(net,'is_rom',lambda _:False)(proj) and not net.fork_slug(proj['slug']):
        from . import allocation_search
        evidence = allocation_search.saved(net,proj,fn,source) or {}
        rows = evidence.get('rows',[])
        if rows and allocation_evidence.diagnose(rows,proj['arch'])['register_cycles']:
            lines.append(allocation_evidence.text(rows,proj['arch']))
            if clue := allocation_search.brief(net,proj,fn,source,rows):lines.append(clue)
    experiment_evidence.prepare(net)
    rows = net.project_store(fn['project_id']).all('SELECT family,attempted,same_output,no_edit,improved,worse,failed FROM repair_input_effects WHERE function_id=? AND scope=? ORDER BY attempted DESC LIMIT 3',fn['id'],experiment_evidence.scope(net,proj,fn))
    if rows:
        lines.append('Current-input local trials: '+'; '.join(f"{r['family']}: {r['attempted']} proposals, {r['same_output']} identical output, {r['no_edit']} no edits, {r['worse']} worse, {r['failed']} failed, {r['improved']} improvements" for r in rows)+'. Unchanged source/build inputs need not repeat ineffective trials; a changed lifetime or declaration shape starts fresh evidence. These families remain available.')
    cached = net.project_store(fn['project_id']).all("SELECT family,COUNT(*) AS n FROM agent_family_targets WHERE function_id=? AND scope=? AND family IN ('compiled-miss','compiled-error') GROUP BY family", fn['id'], agent_tokens.target_scope(net,fn,''))
    if cached:
        lines.append('Previously tested candidates under current build inputs: '+', '.join(f"{r['n']} {r['family']}" for r in cached)+'. Local repairs skip these exact source hashes; change source/build inputs before retrying. This is not proof that a whole family is exhausted.')
    return '\n\nLocal search evidence:\n'+'\n'.join(lines)+'\nPage full evidence if needed.' if lines else ''
