"""Explicitly select a reviewed correction even when an invalid historical candidate scored better."""
import hashlib
import json

from . import candidate_quality
from .network import NetworkError


def adopt(net, slug, function, source_hash, attempt_id):
    fn = net._function(function, slug)
    agent, _ = net.local_agent('candidate-review')
    with net.attempt_turn(agent, fn):
        return _adopt(net, slug, function, source_hash, attempt_id, agent)


def _adopt(net, slug, function, source_hash, attempt_id, agent):
    fn = net._function(function, slug)
    reports = [r for r in candidate_quality.pending(net, fn) if r['source_hash'] == source_hash]
    if not reports or not isinstance(attempt_id, int) or isinstance(attempt_id, bool):
        raise NetworkError('Refresh the quality report and choose one of its saved alternatives.')
    if attempt_id not in {ident for r in reports for ident in r['alternatives']}:
        raise NetworkError('Choose an alternative attached to this quality report.')
    alternative = net.store.one('SELECT * FROM attempts WHERE id=? AND function_id=? AND compiled=1 AND score IS NOT NULL',
                                attempt_id, fn['id'])
    if not alternative or not alternative['source']:
        raise NetworkError('This alternative is unavailable. Refresh the quality report.')
    source = alternative['source']
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == source_hash or any(r['source_hash'] == digest and r.get('review', 'pending') == 'pending'
                                   for r in candidate_quality.records(net, fn)):
        raise NetworkError('This alternative also has an unresolved quality report. Review it first.')
    if slug in net.links:
        raise NetworkError('Select corrective alternatives on the room host’s project.')
    proj = net.project_row(slug)
    context = net._context(proj['id'], fn['unit'])
    # The normal judges check ownership and, for imported projects, rebuild the unit and protect sibling matches.
    try:
        result = net.attempt(agent, function, source, slug)
        if result['score'] is None:
            raise NetworkError('The alternative did not compile in the current context. Inspect its new attempt.')
        with net.store.tx() as db:
            current = db.execute('SELECT * FROM functions WHERE id=?', (fn['id'],)).fetchone()
            config = db.execute('SELECT config FROM projects WHERE id=?', (proj['id'],)).fetchone()['config']
            fresh_context = net._context(proj['id'], fn['unit'])
            before, after = json.loads(proj['config']), json.loads(config)
            # Autosave advances this manifest counter, including while the normal judge runs.
            # It cannot affect compilation; every other config field and the context remain guarded.
            before.pop('progress_revision', None)
            after.pop('progress_revision', None)
            if before != after or dict(context or {}) != dict(fresh_context or {}):
                raise NetworkError('Build settings changed during review. Recheck the alternative before selecting it.')
            current_hash = hashlib.sha256((current['best_source'] or '').encode()).hexdigest()
            if current_hash not in (source_hash, digest):
                raise NetworkError('The selected source changed during review. Refresh before replacing it.')
            if current['status'] == 'matched' and current_hash != digest:
                raise NetworkError('This function matched meanwhile; its matched code was preserved.')
            if current['status'] not in ('open', 'matched'):
                raise NetworkError('This function was set aside meanwhile. Refresh the report.')
            db.execute('UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=?',
                       (source, result['score'], agent.id, fn['id']))
            from .app.permuter_inputs import external_seed
            external_seed(db, current, source, agent.name)
        # Retain the old warning and attempts; a new source hash naturally resolves the old current-source concern.
        net.add_note(agent, slug, f'Review selected corrective alternative attempt {attempt_id}; rejudged score '
                     f'{result["score"]}, replacing reported source {source_hash[:20]}. Selection is a reviewer '
                     'decision, not an automatic semantic proof.', function)
        from . import claim_stalls
        claim_stalls.resume(net, net._function(function, slug))
        return {'selected': function, 'score': result['score'], 'matched': result['matched']}
    finally:
        held = net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id)
        if held:
            net.release(agent, function, '', slug)
