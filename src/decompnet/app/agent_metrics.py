"""Resettable card counters retain attribution, transcripts and lifetime billing records."""
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
    transactional = getattr(net.project_store(pid)._local, 'in_tx', False)
    with lock:
        revision = net.project_store(pid).one('SELECT revision,rewritten FROM history_revisions WHERE project_id=?', pid)
        revision = tuple(revision) if revision else (0, 0)
        key = tuple(boundaries)
        old = None if transactional else cache.get('state')
        incremental = old is not None and old['key'] == key and append_only(net.project_store(pid), pid, old['revision'], revision, old['last'])
        if not incremental:
            old = {'key': key, 'revision': None, 'last': 0, 'best': {}, 'agents': {}}
        if old['revision'] != revision:
            limits = dict(boundaries)
            rows = net.project_store(pid).all(
                'SELECT t.id,t.function_id,t.agent_id,t.score,t.compiled,t.created_at,f.n_insns '
                'FROM main.functions f JOIN main.attempts t ON t.function_id=f.id '
                'WHERE f.project_id=? AND t.id>? ORDER BY t.function_id,t.id', pid, old['last'])
            from .project_labels import names
            labels = names(net, (r['agent_id'] for r in rows))
            rows = [dict(row) | {'agent':labels.get(row['agent_id'])} for row in rows]
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
        matched = {(r['id'], r['matched_agent_id']) for r in net.project_store(pid).all(
            "SELECT id,matched_agent_id FROM main.functions WHERE project_id=? AND status='matched'", pid)}
        board = {name: {'agent': name, 'compiles': c['compiles'], 'worked': len(c['worked']),
                        'improved': len(c['improved']), 'matched': len(c['zero'] & matched),
                        'last_improvement': c['last_improvement']} for name, c in old['agents'].items()}
    import json
    workers = {r['name']:r['id'] for r in net.store.all(
        'SELECT id,name FROM agents WHERE name IN (SELECT value FROM json_each(?))',
        json.dumps([s.agent_name for s in sessions]))}
    placeholders = ','.join('(?,?)' for _ in sessions)
    counts = {r['agent_id']:r['n'] for r in net.project_store(pid).all(
        f'WITH boundary(agent_id,after_id) AS (VALUES {placeholders}) '
        'SELECT e.agent_id,COUNT(DISTINCT e.detail) n FROM events e JOIN boundary b ON b.agent_id=e.agent_id '
        "WHERE e.project_id=? AND e.kind='readable' AND e.id>b.after_id GROUP BY e.agent_id",
        *(v for s in sessions for v in (workers.get(s.agent_name, -1), int(s.options.get('_metrics', {}).get('event', 0)))), pid)}
    polished = {name:counts[aid] for name, aid in workers.items() if aid in counts}
    for s in sessions:
        row = board.setdefault(s.agent_name, {'matched': 0, 'improved': 0, 'worked': 0, 'compiles': 0, 'last_improvement': None})
        row['polished'] = polished.get(s.agent_name, 0)
    return board


def lifetime_cost(manager, s, history_version=None, records=None):
    """Read one helper's billing evidence without computing project/card metrics."""
    from .. import agents
    from . import session_history
    from .agents import TOKEN_KINDS
    if records is None:
        history_version, records = session_history.records(manager.net, s.id, s.agent_name, project=s.slug)
    def history_costs(records=records, kind=s.kind):
        estimates = [agents.get(kind).estimate_cost(r["model"], {
            k: r[k + "_tokens"] for k in TOKEN_KINDS} | {"tokens": r["tokens"]}) for r in records if r["tokens"]]
        return estimates, sum(r['tokens'] for r in records), {r['id'] for r in records}
    saved_costs, recorded_tokens, session_ids = manager.net.project_store(s.slug).read_cache.get(
        ('helper-history-cost',s.id,s.agent_name,s.kind),history_version,history_costs)
    estimates = list(saved_costs)  # Active usage is live, never appended to the cached completed totals.
    if s.record and s.session_id not in session_ids and s.usage["tokens"]:
        active = manager.net.project_store(s.slug).one("SELECT model FROM helper_sessions WHERE id=?", s.session_id)
        model = active["model"] if active else s.model
        estimates.append(agents.get(s.kind).estimate_cost(model, s.usage))
    if s.record and s.session_id not in session_ids:
        recorded_tokens += s.usage["tokens"]
    cost = (sum(estimates) if estimates and all(x is not None for x in estimates)
                             and recorded_tokens == s.tokens else None)
    return cost


def reset(manager, sid):
    with manager._lock:
        s = manager.sessions.get(sid)
        if s is None:
            raise ValueError('This agent no longer exists. Refresh the agents list.')
        estimate = lifetime_cost(manager, s)
        missing = object()
        previous = s.options.get('_metrics', missing)
        s.options['_metrics'] = {
            'estimated_cost': estimate,
            'at': time.time(),
            'attempt': manager.net.project_store(s.slug).one('SELECT COALESCE(MAX(id),0) AS n FROM main.attempts')['n'],
            'event': manager.net.project_store(s.slug).one('SELECT COALESCE(MAX(id),0) AS n FROM events')['n'],
            'tokens': s.tokens, 'usd': s.usd, 'runs': s.runs, 'restarts': s.restarts,
        }
        # _save already persists options alongside helper state in one transaction.
        try:manager._save(s)
        except BaseException:
            if previous is missing:s.options.pop('_metrics', None)
            else:s.options['_metrics'] = previous
            raise
        manager._metrics_cache = {}
        return {'metrics_since':s.options['_metrics']['at'], 'metrics_after':s.options['_metrics']['attempt']}
