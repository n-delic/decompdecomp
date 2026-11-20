"""Optional, capped comparison of normal routing and random overlap on hard claims."""
import hashlib
import json
import secrets
from collections import defaultdict
from statistics import fmean

LIMIT = 200


def configure(cfg, budget, *, used=0, pending=0):
    if budget:
        current = cfg.get('routing_experiment') or {}
        if current.get('enabled') and (used < current['budget'] or pending):
            return
        cfg['routing_experiment'] = {'run': secrets.token_hex(12), 'budget': budget, 'enabled': True}
    elif current := cfg.get('routing_experiment'):
        current['enabled'] = False


def arm(run, function_id):
    bucket = hashlib.sha256(f'{run}:{function_id}'.encode()).digest()[0]
    if bucket < 16:
        return 'policy'
    if bucket < 32:
        return 'overlap'
    return ''


def plan(store, proj, preferred, deferred, policy):
    cfg = json.loads(proj['config']).get('routing_experiment') or {}
    if not cfg.get('enabled') or not (preferred or deferred):
        return {}
    run = cfg['run']
    recorded = store.all('SELECT function_id FROM claim_routing_experiments WHERE project_id=? AND run=?',
                         proj['id'], run)
    if len(recorded) >= cfg['budget']:
        return {}
    seen = {r['function_id'] for r in recorded}
    arms = {function_id: side for function_id in (*preferred, *deferred)
            if function_id not in seen and (side := arm(run, function_id))}
    if not arms:
        return {}
    budget_key = json.dumps({key: policy.get(key) for key in (
        'max_tries', 'stall_compiles', 'unchanged_compiles', 'match_below', 'model_untried'
    )}, sort_keys=True)
    return {'run': run, 'arms': arms, 'budget_key': budget_key,
            'normal_preferred': json.dumps(preferred), 'normal_deferred': json.dumps(deferred)}


def available(db, project_id, function_id, trial):
    cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (project_id,)).fetchone()[0])
    current = cfg.get('routing_experiment') or {}
    if not current.get('enabled') or current.get('run') != trial['run']:
        return False
    used, already_sampled = db.execute(
        'SELECT COUNT(*),MAX(function_id=?) FROM claim_routing_experiments WHERE project_id=? AND run=?',
        (function_id, project_id, trial['run'])).fetchone()
    return not already_sampled and used < current['budget']


def start(db, fn, agent, now, trial):
    side = trial['arms'].get(fn['id'])
    if not side:
        return
    db.execute('INSERT OR IGNORE INTO claim_routing_experiments '
               '(project_id,run,function_id,agent_id,claimed_at,arm,budget_key,profile,band) '
               'SELECT ?,?,?,?,?,?,?,profile,band FROM claim_routing_samples '
               'WHERE function_id=? AND agent_id=? AND claimed_at=?',
               (fn['project_id'], trial['run'], fn['id'], agent.id, now, side, trial['budget_key'],
                fn['id'], agent.id, now))


def exclusive(net, proj, function_id, agent_id):
    cfg = net.cfg(proj).get('routing_experiment')
    if not cfg:
        return False
    return net.project_store(proj['id']).one(
        'SELECT 1 FROM claim_routing_experiments WHERE function_id=? '
        'AND agent_id=? AND ended_at IS NULL', function_id, agent_id) is not None


def report(store, proj, config):
    if not config:
        return None
    rows = store.all('SELECT arm,budget_key,claimed_at,ended_at,profile,band,won '
                     'FROM claim_routing_experiments WHERE project_id=? AND run=?', proj['id'], config['run'])
    cohorts = defaultdict(lambda: defaultdict(lambda: {'claims': 0, 'matches': 0, 'seconds': 0}))
    for r in rows:
        if r['ended_at'] is None:
            continue
        group = cohorts[r['profile'], r['band'], r['budget_key']][r['arm']]
        group['claims'] += 1
        group['matches'] += r['won']
        group['seconds'] += max(0, r['ended_at'] - r['claimed_at'])
    paired = [group for group in cohorts.values() if len(group) == 2]
    results = {}
    for side in ('policy', 'overlap'):
        counts = {field: sum(group[side][field] for group in paired) for field in ('claims', 'matches', 'seconds')}
        counts['matches_per_claim'] = fmean(group[side]['matches'] / group[side]['claims'] for group in paired) if paired else None
        timed = [group for group in paired if group['policy']['seconds'] and group['overlap']['seconds']]
        counts['matches_per_helper_hour'] = fmean(
            group[side]['matches'] * 3600 / group[side]['seconds'] for group in timed) if timed else None
        results[side] = counts
    return {'budget': config['budget'], 'enabled': config['enabled'], 'used': len(rows),
            'pending': sum(r['ended_at'] is None for r in rows), 'cohorts': len(paired), 'results': results}
