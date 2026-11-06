"""Resettable card counters retain attribution, transcripts and lifetime billing records."""
import json
import time

from ..history_changes import append_only


def results(net, slug, sessions):
    pid = net.project_row(slug)['id']
    boundaries = [(s.agent_name, int(s.options.get('_metrics', {}).get('attempt', 0))) for s in sessions]
    if not boundaries:
        return {}
    # Cache only measurement metadata. Appends consume the tail; corrections/deletions
    # rebuild it, using the same project revision contract as contributor summaries.
    import threading
    caches = net.__dict__.setdefault('_card_history', {})
    lock, cache = caches.setdefault(pid, (threading.RLock(), {}))
    transactional = getattr(net.store._local, 'in_tx', False)
    with lock:
        revision = net.store.one('SELECT revision,rewritten FROM history_revisions WHERE project_id=?', pid)
        revision = tuple(revision) if revision else (0, 0)
        key = tuple(boundaries)
        old = None if transactional else cache.get('state')
        incremental = old is not None and old['key'] == key and append_only(net.store, pid, old['revision'], revision, old['last'])
        if not incremental:
            old = {'key': key, 'revision': None, 'last': 0, 'best': {}, 'agents': {}}
        if old['revision'] != revision:
            limits = dict(boundaries)
            rows = net.store.all(
                'SELECT t.id,t.function_id,t.agent_id,a.name AS agent,t.score,t.compiled,t.created_at,f.n_insns '
                'FROM functions f JOIN attempts t ON t.function_id=f.id LEFT JOIN agents a ON a.id=t.agent_id '
                'WHERE f.project_id=? AND t.id>? ORDER BY t.function_id,t.id', pid, old['last'])
            for r in rows:
                fid, score, name = r['function_id'], r['score'], r['agent']
                previous = old['best'].get(fid, r['n_insns'] * 100 if r['n_insns'] is not None else None)
                if name in limits and r['id'] > limits[name]:
                    counts = old['agents'].setdefault(name, {'compiles': 0, 'worked': set(),
                                                           'improved': set(), 'zero': set(), 'last_improvement': None})
                    counts['compiles'] += 1
                    counts['worked'].add(fid)
                    if r['compiled'] and score is not None:
                        if score == 0:
                            counts['zero'].add((fid, r['agent_id']))
                        if score >= 0 and previous is not None and score < previous:
                            counts['improved'].add(fid)
                            at = r['created_at']
                            if at is not None:
                                counts['last_improvement'] = max(counts['last_improvement'] or at, at)
                if r['compiled'] and score is not None:
                    old['best'][fid] = min(previous, score) if fid in old['best'] else score
                old['last'] = max(old['last'], r['id'])
            old['revision'] = revision
        if not transactional:
            cache['state'] = old
        # Match ownership can change without adding an attempt, so read it fresh.
        matched = {(r['id'], r['matched_agent_id']) for r in net.store.all(
            "SELECT id,matched_agent_id FROM functions WHERE project_id=? AND status='matched'", pid)}
        board = {name: {'agent': name, 'compiles': c['compiles'], 'worked': len(c['worked']),
                        'improved': len(c['improved']), 'matched': len(c['zero'] & matched),
                        'last_improvement': c['last_improvement']} for name, c in old['agents'].items()}
    placeholders = ','.join('(?,?)' for _ in sessions)
    polished = {r['agent']: r['n'] for r in net.store.all(
        f'WITH boundary(agent,after_id) AS (VALUES {placeholders}) '
        'SELECT a.name AS agent,COUNT(DISTINCT e.detail) AS n FROM events e '
        'JOIN agents a ON a.id=e.agent_id JOIN boundary b ON b.agent=a.name '
        "WHERE e.project_id=? AND e.kind='readable' AND e.id>b.after_id GROUP BY a.name",
        *(v for s in sessions for v in (s.agent_name, int(s.options.get('_metrics', {}).get('event', 0)))), pid)}
    for s in sessions:
        row = board.setdefault(s.agent_name, {'matched': 0, 'improved': 0, 'worked': 0, 'compiles': 0, 'last_improvement': None})
        row['polished'] = polished.get(s.agent_name, 0)
    return board


def reset(manager, sid):
    with manager._lock:
        s = manager.sessions.get(sid)
        if s is None:
            raise ValueError('This agent no longer exists. Refresh the agents list.')
        estimate = next(row['estimated_cost'] for row in manager.snapshot() if row['id'] == sid)
        previous = s.options.get('_metrics', {}).get('estimated_cost', 0)
        estimate = estimate + previous if estimate is not None and previous is not None else None
        s.options['_metrics'] = {
            'estimated_cost': estimate,
            'at': time.time(),
            'attempt': manager.net.store.one('SELECT COALESCE(MAX(id),0) AS n FROM attempts')['n'],
            'event': manager.net.store.one('SELECT COALESCE(MAX(id),0) AS n FROM events')['n'],
            'tokens': s.tokens, 'usd': s.usd, 'runs': s.runs, 'restarts': s.restarts,
        }
        with manager.net.store.tx() as db:
            kept = {k: v for k, v in s.options.items() if k != 'api_key'}
            db.execute('UPDATE helpers SET options=? WHERE id=?', (json.dumps(kept), s.id))
        manager._save(s)
        manager._metrics_cache = {}
