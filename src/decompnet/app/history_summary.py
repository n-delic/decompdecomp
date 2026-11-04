"""Shared contributor rollups read indexed measurements, never historical C source.

A project revision detects appends, deletions, score corrections and function moves.
The cache belongs to a Store, so separate homes cannot share project IDs or results.
"""
import threading
import weakref
from collections import defaultdict

from ..history_changes import append_only

_caches = weakref.WeakKeyDictionary()
_lock = threading.Lock()


def summary(store, project):
    if getattr(store._local, 'in_tx', False):
        # Never publish uncommitted values: a rollback may reuse the same revision later.
        cache, lock = {}, threading.Lock()
    else:
        with _lock:
            cache, lock = _caches.setdefault(store, {}).setdefault(project, ({}, threading.Lock()))
    with lock:
        row = store.one('SELECT revision,rewritten FROM history_revisions WHERE project_id=?', project)
        fingerprint = tuple(row) if row else (0, 0)
        previous = cache.get(project)
        if previous and previous[0] == fingerprint:
            return previous[1]
        incremental = previous is not None and previous[1]['last_id'] is not None and append_only(store, project, previous[0], fingerprint, previous[1]['last_id'])
        state = {k: dict(v) if isinstance(v, dict) else v for k, v in previous[1].items()} if incremental else {}
        changed_agents, changed_functions = set(), set()
        agents = defaultdict(lambda: {'compiles': 0, 'worked': set(), 'improved': set(),
                                     'verified_improved': set(), 'last': None}, state.get('agents', {}))
        recency = state.get('recency', {})
        best, valid_best = state.get('best', {}), state.get('valid_best', {})
        last_id = state.get('last_id')
        since = ' AND t.id>?' if incremental else ''
        args = (project, last_id) if incremental else (project,)
        for row in store.all(
                'SELECT t.id,t.function_id,t.agent_id,t.compiled,t.score,t.score_improved,t.created_at,f.n_insns '
                'FROM functions f JOIN attempts t ON t.function_id=f.id WHERE f.project_id=?' + since +
                ' ORDER BY t.function_id,t.id', *args):
            last_id = row['id'] if last_id is None else max(last_id, row['id'])
            fid, aid, score = row['function_id'], row['agent_id'], row['score']
            if incremental and aid not in changed_agents and aid in agents:
                agents[aid] = {k: set(v) if isinstance(v, set) else v for k, v in agents[aid].items()}
            changed_agents.add(aid)
            agent = agents[aid]
            agent['compiles'] += 1
            agent['worked'].add(fid)
            at = row['created_at']
            if at is not None:
                agent['last'] = max(agent['last'] or at, at)
            if incremental and fid not in changed_functions and fid in recency:
                recency[fid] = dict(recency[fid])
            changed_functions.add(fid)
            recent = recency.setdefault(fid, {'last_checked': None, 'last_improved': None})
            if at is not None:
                recent['last_checked'] = max(recent['last_checked'] or at, at)
            if row['compiled'] and score is not None:
                improved = score >= 0 and score < best.get(fid, (row['n_insns'] or 0) * 100)
                if improved:
                    agent['improved'].add(fid)
                    if at is not None:
                        recent['last_improved'] = max(recent['last_improved'] or at, at)
                if score >= 0:
                    verdict = row['score_improved']
                    if verdict == 1 or (verdict is None and score < valid_best.get(fid, (row['n_insns'] or 0) * 100)):
                        agent['verified_improved'].add(fid)
                    valid_best[fid] = min(valid_best.get(fid, score), score)
                best[fid] = min(best.get(fid, score), score)
        result = {'agents': dict(agents), 'recency': recency, 'best': best, 'valid_best': valid_best, 'last_id': last_id}
        cache[project] = (fingerprint, result)
        return result
