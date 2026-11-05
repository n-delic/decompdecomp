"""Offline ROM deep search: local leases and durable, input-bound room deliveries."""
import json
import time

from .. import match_bundle
from ..network import NetworkError
from ..project import compiler_for


def unavailable(error):
    # Also recognize persisted failures written before transport had its own exception.
    from .collab import Refused
    if isinstance(error,Refused):return False
    text=str(error).lower()
    return any(s in text for s in ("host isn't reachable", "can't reach the host", 'host connection temporarily unavailable', 'host offline'))


def offline(link):
    return unavailable(getattr(link,'error',''))


def disconnected(search, link, error):
    if not unavailable(error):raise error
    link.error=str(error)
    if not getattr(search,'room_offline',False):
        search.log('Host unavailable — continuing with the local ROM. Results are saved for synchronization.')
    search.room_offline=True


def submit(search, link, agent, fn, source, evidence=None):
    from . import room_matches
    if evidence is None:evidence=room_matches.packet(link.net,link.slug,fn['name'],source)
    if not offline(link):
        try:return room_matches.send(link,agent,fn['name'],source,evidence=evidence)
        except NetworkError as error:disconnected(search,link,error)
    room_matches.save(link.net,link.slug,'client',agent,fn['name'],source,evidence,link.url+'/'+link.room)
    search.log('Locally verified exact candidate queued for host verification: '+fn['name'])
    return {'matched':False,'pending':True,'reason':'Host unavailable; exact evidence saved for automatic retry.'}


def best(search, link, agent, fn, source, score):
    from . import room_matches
    net=link.net
    key=f'deep-room-best:{fn["project_id"]}:{fn["id"]}'
    item={'function':fn['name'],'agent':agent.name,'source':source,'score':score,
          'evidence':room_matches.packet(net,link.slug,fn['name'],source),'room':link.url+'/'+link.room,'updated_at':time.time()}
    with net.store.tx() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(item)))
    if not offline(link):
        try:
            result=link._req('POST','/collab/best',item)
            if result.get('ok'):
                with net.store.tx() as db:db.execute('DELETE FROM settings WHERE key=? AND value=?',(key,json.dumps(item)))
        except NetworkError as error:disconnected(search,link,error)


def comparable(net, slug, function, evidence):
    """Near-miss scores transfer only between identical target/header/compiler inputs."""
    if not isinstance(evidence,dict):return False
    fn=net._function(function,slug);proj=net.project_row(slug)
    own=compiler_for(net.cfg(proj),fn['vram'],fn)
    return (evidence.get('target_sha256')==match_bundle.digest(fn['target'] or b'')
            and evidence.get('vram')==fn['vram']
            and evidence.get('compiler')=={k:own[k] for k in ('toolchain','flags')}
            and evidence.get('header_sha256')==match_bundle.digest(net._context(proj['id'],fn['unit'])['header']))


def pump(link):
    proj=link.net.project_row(link.slug)
    prefix=f'deep-room-best:{proj["id"]}:'
    rows=link.net.store.all('SELECT key,value FROM settings WHERE key LIKE ? ORDER BY json_extract(value,\'$.updated_at\') LIMIT 8',prefix+'%')
    for row in rows:
        item=json.loads(row['value'])
        if item['room']!=link.url+'/'+link.room:continue
        result=link._req('POST','/collab/best',item)
        if result.get('ok'):
            with link.net.store.tx() as db:db.execute('DELETE FROM settings WHERE key=? AND value=?',(row['key'],row['value']))
        else:
            item.update(updated_at=time.time(),reason=result.get('reason','Host inputs differ; local candidate retained.'))
            with link.net.store.tx() as db:
                db.execute('UPDATE settings SET value=? WHERE key=? AND value=?',(json.dumps(item),row['key'],row['value']))
