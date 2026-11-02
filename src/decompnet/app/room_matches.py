"""Durable room exact-candidate evidence; only the host's linked-byte judge accepts it."""
import hashlib
import json
import re
import time

from .. import candidate_scope, match_bundle, toolchains
from ..network import Agent, NetworkError
from ..project import compiler_for
from . import prepared_evidence, source_preparation

PREFIX = 'room-match:'


def packet(net, slug, function, source):
    fn = net._function(function, slug)
    proj = net.project_row(slug)
    if net.fork_slug(slug):
        return None
    row = net.store.one('SELECT * FROM verified_matches WHERE function_id=?', fn['id']) if net.store.one(
        "SELECT 1 FROM sqlite_master WHERE name='verified_matches'") else None
    if row and row['source'] == source:
        details = json.loads(row['details'])
        return {k: details.get(k) for k in ('compiler_identity','abi','vram','target_sha256','source_sha256')} | {
            'compiler':{k:(details.get('compiler') or compiler_for(net.cfg(proj),fn['vram'])).get(k) for k in ('toolchain','flags')},
            'header':row['header'],'header_sha256':match_bundle.digest(row['header'])}
    cfg = net.cfg(proj)
    return {'compiler':{k:compiler_for(cfg,fn['vram'])[k] for k in ('toolchain','flags')}, 'header':net._context(proj['id'],fn['unit'])['header'],
            'compiler_identity':match_bundle.compiler_identity(compiler_for(cfg,fn['vram'])),
            'abi':match_bundle.abi(proj,cfg),'vram':fn['vram'],
            'target_sha256':match_bundle.digest(fn['target'] or b''),'source_sha256':match_bundle.digest(source),
            'header_sha256':match_bundle.digest(net._context(proj['id'],fn['unit'])['header'])}


def save(net, slug, direction, agent, function, source, evidence, room=''):
    if not isinstance(source,str) or not source.strip() or len(source.encode())>262144:
        raise NetworkError('Room candidate source must be nonempty and at most 256 KB.')
    if evidence is not None and (not isinstance(evidence,dict) or len(json.dumps(evidence).encode())>1200000):
        raise NetworkError('Room match evidence must be an object of at most 1.2 MB.')
    proj=net.project_row(slug);fn=net._function(function,slug)
    key=PREFIX+str(proj['id'])+':'+hashlib.sha256(json.dumps([direction,room,agent.id,fn['id'],source,evidence],sort_keys=True).encode()).hexdigest()
    old=net.store.one('SELECT value FROM settings WHERE key=?',key)
    if old:
        item=json.loads(old['value'])
        if item['status']=='accepted':
            item.update(source=source,evidence=evidence,status='pending',reason='Rechecking resubmitted saved evidence')
            update(net,key,item)
        return key,item
    item={'function_id':fn['id'],'function':function,'agent_id':agent.id,'agent':agent.name,'source':source,
          'evidence':evidence,'direction':direction,'room':room,'status':'pending','reason':'Awaiting host verification',
          'created_at':time.time(),'tried':[]}
    update(net,key,item)
    return key,item


def update(net,key,item):
    item['updated_at']=time.time()
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(item)))


def pending(net,slug, direction=None):
    pid=net.project_row(slug)['id']
    out=[]
    for row in net.store.all('SELECT key,value FROM settings WHERE key LIKE ?',PREFIX+str(pid)+':%'):
        item=json.loads(row['value'])
        if item['status']=='pending' and (direction is None or item['direction']==direction):
            out.append(dict(item,key=row['key']))
    return sorted(out,key=lambda r:r['created_at'])


def public(net,slug):
    return [{k:r.get(k) for k in ('key','function','agent','direction','reason','updated_at')} for r in pending(net,slug)]


def settings(net,proj,profile):
    if not isinstance(profile,dict) or set(profile)-{'toolchain','flags','name'}:
        raise NetworkError('Unsupported room compiler profile; choose a registered compiler and flags.')
    tid,flags=profile.get('toolchain'),profile.get('flags')
    if not isinstance(tid,str) or not isinstance(flags,list) or len(flags)>100 or not all(isinstance(f,str) and len(f)<160 for f in flags):
        raise NetworkError('Invalid room compiler ID or flags.')
    if tid not in {c.id for c in net.console(proj).compilers}:
        raise NetworkError('This compiler is not supported by the host project console.')
    if not toolchains.installed(tid):
        raise NetworkError('Host needs compiler '+tid+' installed in Compilers & tools; no download was started.')
    # Only host-known scalar flags may arrive over the room boundary, never plugins, paths or command overrides.
    known={f for _,fs in net._comparison_candidates(proj,all_installed=True) for f in fs}
    if any(f not in known and not re.fullmatch(r'-(?:G\d+|g[0-3]?|O[0-3sg])',f) for f in flags):
        raise NetworkError('Room profile contains flags not offered by the host. Review compiler settings before retrying.')
    return {'toolchain':tid,'flags':flags}


def verify(net,slug,agent,function,source,evidence=None):
    key,item=save(net,slug,'host',agent,function,source,evidence)
    result=retry(net,slug,key,recover_profiles=bool(evidence))
    if not result['matched']:
        # The client finished its local exact candidate. A pending host review must not strand its next claim.
        with net.store.tx() as db:
            removed=db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(item['function_id'],agent.id)).rowcount
            if removed:net._event(db,net.project_row(slug)['id'],agent.id,'release',function)
    return result


def retry(net,slug,key, *, recover_profiles=True):
    proj=net.project_row(slug)
    if not key.startswith(PREFIX+str(proj['id'])+':'):
        raise NetworkError('Choose a submission belonging to this project.')
    row=net.store.one('SELECT value FROM settings WHERE key=?',key)
    if not row:raise NetworkError('Room submission is no longer available.')
    item=json.loads(row['value']);fn=net.store.one('SELECT * FROM functions WHERE id=? AND project_id=?',item['function_id'],proj['id'])
    if not fn:raise NetworkError('Room submission function was removed.')
    agent_row=net.store.one('SELECT id,name,owner,trusted FROM agents WHERE id=?',item['agent_id'])
    if not agent_row:raise NetworkError('Room submission agent no longer exists.')
    agent=Agent(agent_row['id'],agent_row['name'],agent_row['owner'],bool(agent_row['trusted']))
    result={'matched':False,'score':None,'pending':True,'submission':key}
    try:
        if fn['status']=='matched':
            result.update(matched=True,pending=False)
        else:
            own=compiler_for(net.cfg(proj),fn['vram'])
            if toolchains.installed(own['toolchain']):
                res=net.attempt(agent,fn['name'],item['source'],slug)
            else:
                # An installed submitted profile may still verify without the host's default compiler.
                with net.store.tx() as db:
                    net._expire_claims(db)
                    claim=db.execute('SELECT agent_id FROM claims WHERE function_id=?',(fn['id'],)).fetchone()
                    if claim and claim['agent_id']!=agent.id:
                        raise NetworkError('This function is claimed by another worker; retry when released.')
                    if not claim and db.execute('SELECT 1 FROM claims WHERE agent_id=?',(agent.id,)).fetchone():
                        raise NetworkError('Submission agent holds another claim; retry when released.')
                    if not claim:
                        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],agent.id,time.time(),time.time()+net.claim_ttl))
                        net._event(db,proj['id'],agent.id,'claim',fn['name'])
                res={'matched':False,'score':None}
            result.update(score=res['score'],matched=res['matched'])
            fn=net._function(fn['name'],slug);proj=net.project_row(slug)
            if not res['matched']:
                evidence=item.get('evidence')
                header=net._context(proj['id'],fn['unit'])['header']
                if evidence:
                    if (evidence.get('source_sha256')!=match_bundle.digest(item['source']) or
                        evidence.get('target_sha256')!=match_bundle.digest(fn['target'] or b'') or evidence.get('vram')!=fn['vram']):
                        raise NetworkError('Client source/target identity differs from the host. Resubmit from the same game/function.')
                    header=evidence.get('header')
                    if (not isinstance(header,str) or len(header.encode())>source_preparation.MAX_HEADER or
                        evidence.get('header_sha256',match_bundle.digest(header))!=match_bundle.digest(header)):
                        raise NetworkError('Invalid room header evidence.')
                    profiles=[settings(net,proj,evidence.get('compiler'))]
                else:
                    if not recover_profiles:
                        raise NetworkError('Older client supplied only C; compiler/header evidence is unavailable. Use Review pending matches → Retry to search installed host profiles, or update the client.')
                    # Older clients sent only C. Explicit retries walk bounded installed host profiles.
                    profiles=[{'toolchain':tc,'flags':list(flags)} for tc,flags in net._comparison_candidates(proj,all_installed=True)]
                identities={p['toolchain']:match_bundle.compiler_identity(p) for p in profiles}
                scope=hashlib.sha256((candidate_scope.key(net,proj,fn)+json.dumps(identities,sort_keys=True)).encode()).hexdigest()
                if item.get('scope')!=scope:item.update(scope=scope,tried=[])
                tested=0
                for profile in profiles:
                    marker=json.dumps(profile,sort_keys=True)
                    if marker in item['tried']:continue
                    if tested>=6:break
                    tested+=1
                    try:
                        expected=source_preparation.inputs(net,net.project_row(slug),net._function(fn['name'],slug))
                        prepared_evidence.promote_measured(net,net.project_row(slug),net._function(fn['name'],slug),
                                                          item['source'],header,profile,agent,expected)
                        result.update(matched=True,score=0,pending=False)
                        break
                    except NetworkError as error:
                        item['reason']=str(error)
                        if not any(word in str(error).lower() for word in ('claim','changed','expired','busy')):
                            item['tried'].append(marker)
                if not result['matched']:
                    item['reason']='Host has not verified this source/header/profile. '+item.get('reason','')+f" Tried {len(item['tried'])}/{len(profiles)} profiles in this context. Retry continues the bounded search or rechecks changed inputs; evidence retained."
    except (NetworkError,ValueError,toolchains.ToolchainError) as error:
        item['reason']=str(error)
    if result['matched']:
        item.update(status='accepted',reason='Host verified exact bytes',source='',evidence=None,tried=[])
        result['pending']=False
    update(net,key,item)
    result['reason']=item['reason']
    return result


def send(link,agent,function,source):
    net,slug=link.net,link.slug
    evidence=packet(net,slug,function,source)
    room=link.url+'/'+link.room
    key,item=save(net,slug,'client',agent,function,source,evidence,room)
    try:
        result=link._req('POST','/collab/attempt',{'agent':agent.name,'function':function,'source':source,'evidence':evidence})
    except NetworkError as error:
        item['reason']=str(error);update(net,key,item);raise
    item.update(status='accepted' if result.get('matched') else 'pending',reason=result.get('reason') or 'Host did not confirm the match; evidence retained.')
    if result.get('matched'):item.update(source='',evidence=None)
    update(net,key,item)
    return result


def recover_client(link,state):
    """Queue saved local matches absent from a full host snapshot before host profiles overwrite local settings."""
    accepted={r['vram'] for r in state.get('matched',[])}
    agent,_=link.net.local_agent('saved-progress')
    proj=link.net.project_row(link.slug)
    for fn in link.net.store.all("SELECT name,vram,matched_source FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL",proj['id']):
        if fn['vram'] not in accepted:
            evidence=packet(link.net,link.slug,fn['name'],fn['matched_source'])
            key,item=save(link.net,link.slug,'client',agent,fn['name'],fn['matched_source'],evidence,link.url+'/'+link.room)
            if item['status']=='accepted':
                item.update(status='pending',reason='Host no longer reports this match; resubmitting saved evidence.')
                update(link.net,key,item)


def resend(link,key):
    row=link.net.store.one('SELECT value FROM settings WHERE key=?',key)
    if not row:raise NetworkError('Queued room match no longer exists.')
    item=json.loads(row['value'])
    if item['direction']!='client' or item['room']!=link.url+'/'+link.room:
        raise NetworkError('This match belongs to a different room.')
    agent_row=link.net.store.one('SELECT id,name,owner,trusted FROM agents WHERE id=?',item['agent_id'])
    if not agent_row:raise NetworkError('Submission agent is no longer available.')
    # Preserve the originally successful evidence, even after host settings have synced locally.
    result=link._req('POST','/collab/attempt',{'agent':agent_row['name'],'function':item['function'],
                                           'source':item['source'],'evidence':item['evidence']})
    item.update(status='accepted' if result.get('matched') else 'pending',reason=result.get('reason') or 'Awaiting host verification')
    if result.get('matched'):item.update(source='',evidence=None)
    update(link.net,key,item)
    return result


def pump(link):
    if time.time()-getattr(link,'_match_retry_at',0)<60:return
    link._match_retry_at=time.time()
    rows=[r for r in pending(link.net,link.slug,'client') if r['room']==link.url+'/'+link.room]
    rows.sort(key=lambda r:r.get('updated_at',r['created_at']))
    if rows:resend(link,rows[0]['key'])


def sync_inputs(net,slug,state):
    """Mirror authoritative host compiler ranges and private function headers before green statuses."""
    profiles=state.get('compiler_settings')
    contexts=state.get('function_contexts') or []
    if not profiles and not contexts:return
    proj=net.project_row(slug)
    with net.store.tx() as db:
        if profiles:
            cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=?',(proj['id'],)).fetchone()['config'])
            cfg['compiler']=profiles['compiler'];cfg['compiler_ranges']=profiles.get('compiler_ranges',[])
            db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
        for item in contexts:
            fn=db.execute('SELECT * FROM functions WHERE project_id=? AND vram=?',(proj['id'],item['vram'])).fetchone()
            if not fn or not isinstance(item.get('header'),str):continue
            unit='room_verified_'+str(fn['id'])
            old=db.execute('SELECT header FROM context_versions WHERE project_id=? AND unit=? ORDER BY version DESC LIMIT 1',(proj['id'],unit)).fetchone()
            if not old or old['header']!=item['header']:
                version=net._next_version(db,proj['id'])
                db.execute('INSERT INTO context_versions(project_id,version,header,message,created_at,unit) VALUES(?,?,?,?,?,?)',
                           (proj['id'],version,item['header'],'Host-verified room context',time.time(),unit))
            db.execute('UPDATE functions SET unit=? WHERE id=?',(unit,fn['id']))


def recover_legacy(net,slug):
    """Old room clients only asked the host to compile when they reported a match."""
    proj=net.project_row(slug)
    rows=net.store.all("SELECT t.agent_id,t.source,f.name FROM attempts t JOIN functions f ON f.id=t.function_id "
                       "WHERE t.id IN (SELECT MAX(a.id) FROM attempts a JOIN functions f ON f.id=a.function_id "
                       "JOIN agents g ON g.id=a.agent_id WHERE f.project_id=? AND f.status='open' "
                       "AND g.origin=? AND a.via='agent' AND a.source IS NOT NULL GROUP BY f.id) LIMIT 20",
                       proj['id'],'collab:'+slug)
    for row in rows:
        a=net.store.one('SELECT id,name,owner,trusted FROM agents WHERE id=?',row['agent_id'])
        agent=Agent(a['id'],a['name'],a['owner'],bool(a['trusted']))
        save(net,slug,'host',agent,row['name'],row['source'],None)
    return len(rows)
