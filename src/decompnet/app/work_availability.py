"""Short, revision-scoped idle receipts; never suppress explicit claims or held work."""
import hashlib
import json
import time


def version(net, agent, slug):
    pid=net.project_row(slug)['id']
    claims=net.store.all('SELECT c.function_id,c.agent_id,c.expires_at FROM claims c JOIN functions f '
                         'ON f.id=c.function_id WHERE f.project_id=? AND c.expires_at>? ORDER BY c.function_id',pid,time.time())
    from .. import claim_stalls
    pauses=net.store.all("SELECT s.key,s.value FROM settings s JOIN functions f ON "
                         "s.key=? || f.id OR s.key GLOB ? || f.id || ':*' WHERE f.project_id=? ORDER BY s.key",
                         claim_stalls.PREFIX,claim_stalls.PREFIX,pid)
    regions=net.store.all('SELECT key,value FROM settings WHERE key LIKE ? ORDER BY key',f'data-region:{pid}:%')
    link=net.links.get(slug)
    return hashlib.sha256(repr((net.store.read_version('inputs',pid),net.selection(slug,agent.name),
                               [tuple(r) for r in pauses],[tuple(r) for r in regions],repr(claim_stalls.repair_rules()),
                               [tuple(r) for r in claims],repr(link.claims) if link else '')).encode()).hexdigest()[:20]


def held(net, agent):
    return bool(net.store.one('SELECT 1 FROM claims WHERE agent_id=? AND expires_at>?',agent.id,time.time()))


def cached(net, agent, slug):
    record=getattr(net,'_idle_claims',{}).get((agent.id,slug))
    if record and not held(net,agent) and record['until']>time.monotonic() and record['revision']==version(net,agent,slug):
        return receipt(record)
    return None


def receipt(record):
    return 'Work availability: '+json.dumps({'state':'waiting','retry_after_seconds':30,
        'work_revision':record['revision'],'reason':record['reason'],'counts':record['counts']},separators=(',',':'))+'\nNo eligible work. End this session; the app checks again without a model request. Explicit named claims remain available.'


def observe(net, agent, slug, text):
    if not text.startswith(('Nothing to claim','Nothing left to claim')) or held(net,agent):
        getattr(net,'_idle_claims',{}).pop((agent.id,slug),None)
        from .session_budget import current
        budget=current(net,agent,slug)
        if budget and held(net,agent):
            budget.waiting=False
        return text
    why,_=net.selection_wait_reason(slug,agent)
    pid=net.project_row(slug)['id']
    counts=net.store.one("SELECT COUNT(*) AS open,SUM(EXISTS(SELECT 1 FROM claims c WHERE c.function_id=f.id AND c.expires_at>?)) AS held "
                          "FROM functions f WHERE project_id=? AND status='open' AND gone_mark IS NULL",time.time(),pid)
    record={'revision':version(net,agent,slug),'reason':why,'until':time.monotonic()+30,
            'counts':{'eligible':0,'open':counts['open'],'held':counts['held'] or 0}}
    cache=net.__dict__.setdefault('_idle_claims',{})
    cache[agent.id,slug]=record
    while len(cache)>64:
        del cache[next(iter(cache))]
    from .session_budget import current
    budget=current(net,agent,slug)
    if budget:
        budget.waiting=True
    return receipt(record)
