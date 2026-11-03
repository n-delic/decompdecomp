"""Bounded build-scoped search notes for agents: useful differences without full assembly replay."""
import hashlib

from . import agent_tokens, allocation_evidence, experiment_evidence, permuter_focus, region_repairs, repair_ranking

LIMIT = 64


def remember(net, agent, proj, fn, source, score):
    if score is None:
        return
    cache = net.__dict__.setdefault('_search_evidence',{})
    current = net.store.one('SELECT * FROM functions WHERE id=?',fn['id']) or fn
    key = (fn['id'],experiment_evidence.build_scope(net,proj,current))
    pool = cache.setdefault(key,{})
    rows = permuter_focus.normalized(repair_ranking.evidence(net,fn['id'],agent.id))
    pool[hashlib.sha256(source.encode()).hexdigest()] = {'score':score,'rows':rows,'kind':permuter_focus.classify(rows),'allocation':allocation_evidence.diagnose(rows,proj['arch'])}
    while len(pool)>8:
        del pool[next(iter(pool))]
    while len(cache)>LIMIT:
        del cache[next(iter(cache))]


def brief(net, proj, fn):
    """Only current-input evidence is shown. Retained alternatives are hints, not saved winners."""
    source = fn['best_source'] or ''
    key = (fn['id'],experiment_evidence.build_scope(net,proj,fn))
    pool = getattr(net,'_search_evidence',{}).get(key,{})
    current = pool.get(hashlib.sha256(source.encode()).hexdigest())
    lines = []
    if current:
        rows = [r for r in current['rows'] if len(r)==3 and r[0]!=' ']
        from ..diff_summary import advice
        if hint := advice(current['rows']):lines.append(hint)
        clues = allocation_evidence.text(current['rows'],proj['arch'])
        if clues:lines.append(clues)
        if region := region_repairs.summary(current['rows'],proj['arch']):lines.append(region)
        plan=allocation_evidence.plan(current['rows'],proj['arch'])
        next_family=(' Try '+plan['families'][0]+'.') if plan['families'] else ''
        lines.append(f"Remaining mismatch: {plan['category'] or 'mixed'}; {len(rows)} displayed differences.{next_family}")
        lines.extend(f"  {r[0]} {str(r[1])[:80]} => {str(r[2])[:80]}" for r in rows[:4])
        from .repair_sequences import starting
        learned = starting(net,proj,fn,current['rows'])
        if learned:
            lines.append('Previously exact repair sequences start with: '+', '.join(learned[:2])+'. Use local variants when applicable; this is advisory.')
        alternatives = [v for v in pool.values() if v is not current and v['score']>0 and v['score']<=max(1,current['score'])*1.25]
        if alternatives:
            lines.append('Recent tested alternatives (not adopted): '+', '.join(f"{v['kind'] or 'mixed'} score {v['score']} (frame {v.get('allocation',{}).get('candidate_frame','unknown')})" for v in alternatives[-2:]))
    experiment_evidence.prepare(net)
    rows = net.store.all('SELECT family,attempted,same_output,no_edit,improved,worse,failed FROM repair_input_effects WHERE function_id=? AND scope=? ORDER BY attempted DESC LIMIT 3',fn['id'],experiment_evidence.scope(net,proj,fn))
    if rows:
        lines.append('Current-input local trials: '+'; '.join(f"{r['family']}: {r['attempted']} proposals, {r['same_output']} identical output, {r['no_edit']} no edits, {r['worse']} worse, {r['failed']} failed, {r['improved']} improvements" for r in rows)+'. Unchanged source/build inputs need not repeat ineffective trials; a changed lifetime or declaration shape starts fresh evidence. These families remain available.')
    cached = net.store.all("SELECT family,COUNT(*) AS n FROM agent_family_targets WHERE function_id=? AND scope=? AND family IN ('compiled-miss','compiled-error') GROUP BY family", fn['id'], agent_tokens.target_scope(net,fn,''))
    if cached:
        lines.append('Previously tested candidates under current build inputs: '+', '.join(f"{r['n']} {r['family']}" for r in cached)+'. Local repairs skip these exact source hashes; change source/build inputs before retrying. This is not proof that a whole family is exhausted.')
    return '\n\nLocal search evidence:\n'+'\n'.join(lines)+'\nPage full evidence if needed.' if lines else ''
