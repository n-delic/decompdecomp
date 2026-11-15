"""Bounded release receipts. Logical tool retries must not act on a newer lease."""
import functools
import hashlib
import inspect
import json
import threading


def token(net, fn, claim):
    if not claim:
        return ''
    store = net.project_store(fn['project_id'])
    event=store.one("SELECT id FROM events WHERE project_id=? AND agent_id=? AND kind='claim' AND detail=? AND created_at>=? ORDER BY id DESC LIMIT 1",fn['project_id'],claim['agent_id'],fn['name'],claim['claimed_at'])
    return hashlib.sha256(repr((fn['id'],claim['agent_id'],claim['claimed_at'],event['id'] if event else None)).encode()).hexdigest()[:24]


def current(net, fn, agent):
    store = net.project_store(fn['project_id'])
    claim=store.one('SELECT * FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
    return token(net,fn,claim)


def instruction(net, function_id, agent_id, *, project_id=None):
    """Give move-on callers the current lease token, without changing any claim."""
    store=net.project_store(project_id) if project_id is not None else net.store
    fn=store.one('SELECT id,project_id,name FROM functions WHERE id=?',function_id)
    claim=store.one('SELECT * FROM claims WHERE function_id=? AND agent_id=?',function_id,agent_id)
    if not fn or not claim:
        return 'Claim ended; claim another eligible function. Do not release this function again.'
    lease=token(net,fn,claim)
    return (f'release_function(function={json.dumps(fn["name"])}, claim_token={json.dumps(lease)}, '
            'notes="what you tried and what is left")')


def operation(kind, *, protect_new=False):
    """Serialize just this agent/function, without holding the database lock across compile/room work."""
    def decorate(function):
        signature=inspect.signature(function)
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            values=signature.bind(*args,**kwargs);values.apply_defaults();v=values.arguments
            owner=args[0];net=owner if hasattr(owner,'store') else owner.net
            agent=v['agent'];slug=v.get('project') or v.get('slug')
            fn=net._function(v['function'],slug)
            store=net.project_store(fn['project_id'])
            with store.lock:
                lock=net._attempt_locks.setdefault((agent.id,fn['id']),threading.RLock())
            with lock:
                key=f'release-receipts:{agent.id}:{fn["id"]}:{kind}'
                row=store.one('SELECT value FROM settings WHERE key=?',key)
                receipts=json.loads(row['value']) if row else []
                payload={k:v.get(k) for k in ('notes','not_c','needs','move_on','next_experiment') if k in v}
                digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                supplied=v.get('claim_token') or ''
                active=current(net,fn,agent)
                previous=next((r for r in reversed(receipts) if r['request']==digest and (not supplied or supplied==r['claim'])),None)
                if previous and (not active or active==previous['claim'] or supplied==previous['claim']):
                    return previous['reply']
                if previous and protect_new and not supplied:
                    return 'Earlier release replayed. A newer claim is still held and was not released. Read get_candidate and supply its claim_token to release that new claim.\n'+previous['reply']
                if protect_new and active and receipts and not supplied and all(r['claim']!=active for r in receipts):
                    from .network import NetworkError
                    raise NetworkError('This function was claimed again. Supply its current claim_token from get_candidate; an unversioned retry cannot release the new claim.')
                if supplied and supplied!=active:
                    from .network import NetworkError
                    raise NetworkError('The release claim_token is stale or belongs to another claim. No claim was changed; read the current claim before releasing.')
                reply=function(*args,**kwargs)
                # Push-back guidance leaves the same claim held: it is not a completed release.
                after=current(net,fn,agent)
                if (active and after!=active) or (protect_new and not active and not after):
                    saved=reply if len(reply)<=4096 else reply[:4096]+'\n[Earlier reply abbreviated. Read current function/claim state before acting.]'
                    receipts=[r for r in receipts if not (r['claim']==active and r['request']==digest)]
                    receipts.append({'claim':active,'request':digest,'reply':saved})
                    with store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(receipts[-2:])))
                return reply
        return wrapped
    return decorate


@operation('transport', protect_new=True)
def transport_release(net, agent, function, notes, project, not_c=False, claim_token=''):
    """Room requests have the same generation guard as local agent tools; machine calls remain explicit actions."""
    return net.release(agent,function,notes,project,not_c,claim_token=claim_token)
