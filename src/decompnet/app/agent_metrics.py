"""Resettable card counters retain attribution, transcripts and lifetime billing records."""
import json
import time


def results(net, slug, sessions):
    pid = net.project_row(slug)['id']
    boundaries = [(s.agent_name, int(s.options.get('_metrics', {}).get('attempt', 0))) for s in sessions]
    if not boundaries:
        return {}
    placeholders = ','.join('(?,?)' for _ in boundaries)
    rows = net.store.all(
        f'WITH boundary(agent,after_id) AS (VALUES {placeholders}), history AS ('
        'SELECT t.id,t.function_id,t.agent_id,t.score,t.compiled,t.created_at,f.n_insns,f.status,f.matched_agent_id, '
        'MIN(CASE WHEN t.compiled THEN t.score END) OVER (PARTITION BY t.function_id ORDER BY t.id '
        'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous '
        'FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=?) '
        'SELECT a.name AS agent, COUNT(*) AS compiles, COUNT(DISTINCT h.function_id) AS worked, '
        "COUNT(DISTINCT CASE WHEN h.score=0 AND h.compiled AND h.status='matched' "
        'AND h.matched_agent_id=h.agent_id THEN h.function_id END) AS matched, '
        'COUNT(DISTINCT CASE WHEN h.compiled AND h.score>=0 AND '
        'h.score<COALESCE(h.previous,h.n_insns*100) THEN h.function_id END) AS improved, '
        'MAX(CASE WHEN h.compiled AND h.score>=0 AND h.score<COALESCE(h.previous,h.n_insns*100) '
        'THEN h.created_at END) AS last_improvement '
        'FROM history h JOIN agents a ON a.id=h.agent_id JOIN boundary b ON b.agent=a.name '
        'WHERE h.id>b.after_id GROUP BY a.name',
        *(v for pair in boundaries for v in pair), pid)
    board = {r['agent']: dict(r) for r in rows}
    for s in sessions:
        baseline = s.options.get('_metrics', {})
        row = board.setdefault(s.agent_name, {'matched': 0, 'improved': 0, 'worked': 0, 'compiles': 0, 'last_improvement': None})
        row['polished'] = net.store.one(
            "SELECT COUNT(DISTINCT e.detail) AS n FROM events e JOIN agents a ON a.id=e.agent_id "
            "WHERE e.project_id=? AND a.name=? AND e.kind='readable' AND e.id>?",
            pid, s.agent_name, int(baseline.get('event', 0)))['n']
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
