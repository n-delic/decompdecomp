"""Short, measured work packets and one objective for a matching lease."""

import hashlib
import json
import time

from ..network import NetworkError
from . import efficiency_feedback, permuter_focus, region_repairs

GOALS = {
    'call': 'Explain one changed call or argument producer. Check its declaration and test one bounded correction.',
    'register': 'Recover the first differing value lifetime without changing the function contract.',
    'stack': 'Recover one frame or stack access difference. Keep changes confined to the responsible local.',
    'constant': 'Resolve one changed literal, field offset or operand width.',
    'control': 'Recover one divergent block while preserving its side effects.',
    'scheduling': 'Recover the order of one group of independent instructions.',
    '': 'Get one compiling candidate and identify its first measured mismatch.',
}


def _held(store, fn, agent):
    return store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=? AND expires_at>?',
                     fn['id'], agent.id, time.time())


def _recent(store, fn, agent, claim):
    if not claim:
        return []
    return store.all('SELECT a.id,a.score,h.summary FROM main.attempts a LEFT JOIN attempt_hints h ON h.attempt_id=a.id '
                     'WHERE a.function_id=? AND a.agent_id=? AND a.created_at>=? ORDER BY a.id DESC LIMIT 4',
                     fn['id'], agent.id, claim['claimed_at'])


def recovery(rows, recent, measured, best_score, arch):
    if measured.get('build_failed') or (len(recent) >= 2 and all(item['score'] is None for item in recent[:2])):
        return ('Repair the build first. Follow the compiler diagnostic for the missing declaration, type or input. '
                'Use get_function(section="warnings") or section="declarations" for details. Use last_compiling if the last edit broke C.')
    if 'when linking your code' in measured.get('warnings', ''):
        return 'Resolve the linker diagnostic for the named symbol or data before trying instruction changes.'
    score = measured.get('score')
    if score is not None and best_score is not None and score > best_score:
        return 'This candidate scored worse than saved best. Use get_candidate(base="saved_best") to restore that continuation.'
    if measured.get('repeated'):
        return 'This instruction output has already been measured. Change the hypothesis before compiling again.'
    return efficiency_feedback.next_experiment(rows, arch=arch) if rows else 'Compile this revision once to measure its first mismatch.'


def packet(net, agent, slug, name):
    """Use already measured evidence. Reading this packet starts no compiler."""
    from ..release_receipts import current
    from . import agent_evidence, agent_tokens, deep_handoff, search_evidence

    proj, fn = net.project_row(slug, sync=False), net._function(name, slug)
    store = net.project_store(fn['project_id'])
    claim = _held(store, fn, agent)
    if not claim:
        raise NetworkError('Claim this function before requesting its matching work packet.')
    source, revision = agent_tokens.candidate(net, agent, slug, name)
    scope = agent_evidence.build_key(net, fn)[0]
    measured = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id'])) or {}
    if measured.get('source') != source or measured.get('input_scope') != scope:
        baseline_key = agent_evidence.scope(net, agent, fn) + (source_digest(source),)
        measured = getattr(net, '_baseline_evidence', {}).get(baseline_key) or {}
        if measured.get('input_scope') != scope:
            measured = {}
    recent = _recent(store, fn, agent, claim)
    rows = region_repairs.clean(measured.get('rows'))
    progress = objective(net, proj, fn, agent, source, rows, measured, claim)
    measured = {**measured, 'repeated': bool(rows and search_evidence.repeated_hint(net, proj, fn, source))}
    goal = progress['kind']
    if not goal:
        from ..claim_routing import current_focus
        recorded = store.one('SELECT focus,inputs,context_version FROM matching_focus WHERE function_id=?', fn['id'])
        goal = current_focus(store.one, proj, fn, recorded) or ''
    lines = [f'{name} · candidate revision {revision}', f'claim_token: {current(net, fn, agent)}',
             'Objective for this claim. ' + GOALS.get(goal, 'Resolve one ' + goal.lower() + ' mismatch.'),
             recovery(rows, recent, measured, fn['best_score'], proj['arch'])]
    if handoff := deep_handoff.brief(net, proj, fn, source):
        lines.append(handoff)
    if rows:
        lines.append(efficiency_feedback.mismatch(rows))
    if anchors := measured.get('anchors'):
        lines.append('Exact C anchors from this revision. ' + json.dumps(anchors[:2]))
    if recent:
        lines.append('Recent checks in this lease. ' + ' / '.join(
            item['summary'] or f"score {item['score']}" for item in recent[:3]))
    if text := milestone(net, proj, fn, source, scope=scope):
        lines.append(text)
    lines.append('Full assembly and history remain available through get_assembly and get_function(section="history").')
    return '\n'.join(lines) + f'\nCurrent C\n```c\n{source}\n```'


def objective(net, proj, fn, agent, source, rows, measured, claim=None):
    from ..candidate_scope import key

    store = net.project_store(fn['project_id'])
    claim = claim or _held(store, fn, agent)
    if not claim:
        return {'kind': '', 'sites': [], 'scope': '', 'checked': None}
    if len(rows) > 8192:
        return {'kind': permuter_focus.classify(rows, proj['arch']), 'checked': None}
    build = key(net, proj, fn)
    identity = (agent.id, fn['id'])
    active = net.__dict__.setdefault('_matching_objectives', {})
    saved = active.get(identity)
    if not saved or saved['claim'] != claim['claimed_at'] or saved['scope'] != build:
        saved = {'kind': '', 'sites': [], 'scope': build, 'claim': claim['claimed_at'], 'checked': None}
        active[identity] = saved
        while len(active) > 64:
            active.pop(next(iter(active)))
    if rows and measured.get('input_scope') == build:
        if not saved.get('target'):
            resolution = region_repairs.resolution(rows, rows)
            saved.update(kind=permuter_focus.classify(rows, proj['arch']), sites=resolution.get('remaining', [])[:1],
                         target=rows, source=source, checked=None)
        elif source != saved['source']:
            change = region_repairs.resolution(saved['target'], rows)
            if set(saved['sites']).intersection(change.get('removed', [])):
                saved['checked'] = source
            else:
                saved['checked'] = None
    return saved


def record_partial(db, fn, saved):
    """Publish only a measured objective improvement the next claim will inherit."""
    if not saved or not saved.get('checked') or saved['checked'] != fn['best_source']:
        return False
    db.execute('INSERT INTO matching_milestones VALUES(?,?,?,?,?,?,?) ON CONFLICT(function_id) DO UPDATE '
               'SET scope=excluded.scope,kind=excluded.kind,target_index=excluded.target_index,'
               'source_digest=excluded.source_digest,recorded_at=excluded.recorded_at '
               'WHERE matching_milestones.scope!=excluded.scope OR matching_milestones.source_digest!=excluded.source_digest',
               (fn['id'], fn['project_id'], saved['scope'], saved['kind'], saved['sites'][0]+1,
                source_digest(saved['checked']), time.time()))
    return True


def source_digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def observe(net, proj, fn, agent, source, measured):
    rows = region_repairs.clean(measured.get('rows'))
    if fn['status'] != 'open' or not rows or not measured.get('input_scope'):
        return ''
    saved = objective(net, proj, fn, agent, source, rows, measured)
    if saved.get('checked') == source and fn['best_source'] == source:
        store = net.project_store(fn['project_id'])
        with store.tx() as db:
            current = db.execute("SELECT best_source FROM functions WHERE id=? AND status='open'", (fn['id'],)).fetchone()
            if current and current['best_source'] == source and record_partial(db, fn, saved):
                return 'Claim objective measured and kept with saved C. Finish the match or release with the remaining blocker.'
    return 'Claim objective. ' + GOALS[saved['kind']]


def milestone(net, proj, fn, source, scope=None):
    from ..candidate_scope import key

    row = net.project_store(fn['project_id']).one('SELECT * FROM matching_milestones WHERE function_id=?', fn['id'])
    if not row or row['source_digest'] != source_digest(source) or row['scope'] != (scope or key(net, proj, fn)):
        return ''
    return f"Prior claim recovered target instruction {row['target_index']} on the inherited C. Inspect the remaining mismatch."
