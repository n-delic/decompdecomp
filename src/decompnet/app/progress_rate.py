"""Persist a bounded claim/build-scoped low-progress window, without losing the best source."""
import hashlib
import json

MIN_TRIALS=24
WINDOW=12
MIN_GAIN=.01


def plateau(scores):
    if len(scores)<WINDOW or any(s is None or s<0 for s in scores) or min(scores)==0:
        return False
    before=min(scores[:WINDOW//2]);after=min(scores[WINDOW//2:])
    return before>0 and (before-after)/before<=MIN_GAIN


def track(net,agent,slug,fn):
    if net._function(fn['name'],slug)['status']!='open':
        return ''
    claim=net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
    latest=net.store.one('SELECT id,score,compiled FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1',fn['id'],agent.id)
    if not claim or not latest or not latest['compiled'] or latest['score'] is None:
        return ''
    from .. import candidate_scope
    shape=dict(fn);shape['best_source']='';shape['seed']=''
    proj=net._project(slug)
    scope=hashlib.sha256(repr((claim['claimed_at'],candidate_scope.key(net,proj,shape))).encode()).hexdigest()
    key=f'progress-rate:{agent.id}:{fn["id"]}'
    with net.store.tx() as db:
        saved=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        state=json.loads(saved['value']) if saved else {}
        if state.get('scope')!=scope:
            state={'scope':scope,'count':0,'scores':[],'attempt':0}
        if state['attempt']==latest['id']:
            return ''  # Cached/repeated responses are not additional trials.
        state.update(attempt=latest['id'],count=state['count']+1,scores=(state['scores']+[latest['score']])[-WINDOW:])
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(state)))
    threshold=max(MIN_TRIALS,net.selection(slug,agent.name).get('stall_compiles') or 0) if state['count']>=MIN_TRIALS else MIN_TRIALS
    if state['count']>=threshold and plateau(state['scores']):
        from .efficiency_feedback import mismatch
        rows=getattr(net,'_last_diffs',{}).get((agent.id,fn['id']))
        detail=mismatch(rows[0]) if rows else 'Inspect types, calls and structure before repeating local edits.'
        note=f'Low progress after {state["count"]} scored trials: at most 1% score reduction over the recent window. Best source retained. Next evidence: '+detail
        released=net.release(agent,fn['name'],note,slug)
        getattr(net,'_last_diffs',{}).pop((agent.id,fn['id']),None)
        return 'Low-progress handoff: '+released
    return ''
