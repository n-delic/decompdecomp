"""Durable room exact-candidate evidence; only the host's linked-byte judge accepts it."""
import hashlib
import json
import re
import time

from .. import candidate_scope, match_bundle, toolchains
from ..network import Agent, NetworkError
from ..project import compiler_for
from . import build_info, prepared_evidence, source_preparation

PREFIX = 'room-match:'


def failure_reason(item):
    """Older retries wrapped their own summary repeatedly. Keep the actual failure separate from progress."""
    reason=item.get('failure_reason') or item.get('reason','')
    reason=re.sub(r'^(?:Host has not verified this source/header/profile\.\s*)+', '', reason)
    return re.split(r'\s*Tried \d+/\d+ profiles in this context\.', reason, maxsplit=1)[0].strip()


def _packet(net, slug, function, source):
    fn = net._function(function, slug)
    proj = net.project_row(slug)
    if net.fork_slug(slug):
        return None
    row = net.store.one('SELECT * FROM verified_matches WHERE function_id=?', fn['id']) if net.store.one(
        "SELECT 1 FROM sqlite_master WHERE name='verified_matches'") else None
    if row and row['source'] == source:
        details = json.loads(row['details'])
        return {k: details.get(k) for k in ('compiler_identity','abi','vram','target_sha256','source_sha256')} | {
            'compiler':{k:(details.get('compiler') or compiler_for(net.cfg(proj),fn['vram'], fn)).get(k) for k in ('toolchain','flags')},
            'header':row['header'],'header_sha256':match_bundle.digest(row['header'])}
    cfg = net.cfg(proj)
    return {'compiler':{k:compiler_for(cfg,fn['vram'], fn)[k] for k in ('toolchain','flags')}, 'header':net._context(proj['id'],fn['unit'])['header'],
            'compiler_identity':match_bundle.compiler_identity(compiler_for(cfg,fn['vram'], fn)),
            'abi':match_bundle.abi(proj,cfg),'vram':fn['vram'],
            'target_sha256':match_bundle.digest(fn['target'] or b''),'source_sha256':match_bundle.digest(source),
            'header_sha256':match_bundle.digest(net._context(proj['id'],fn['unit'])['header'])}


def packet(net, slug, function, source):
    result = _packet(net, slug, function, source)
    if result is None:
        return None
    fn = net._function(function, slug)
    if net.store.one("SELECT 1 FROM sqlite_master WHERE name='repair_examples'"):
        row = net.store.one("SELECT evidence FROM repair_examples WHERE function_id=? AND json_extract(evidence,'$.after.source_sha256')=? ORDER BY id DESC LIMIT 1",
                            fn['id'], match_bundle.digest(source))
        if row:
            case = json.loads(row['evidence'])
            before = case.get('before') or {}
            attempt = net.store.one('SELECT source,function_id FROM attempts WHERE id=?', before.get('attempt_id')) if before.get('attempt_id') else None
            if attempt and attempt['function_id'] == fn['id'] and match_bundle.digest(attempt['source'] or '') == before.get('source_sha256'):
                settings_before = before.get('settings') or {}
                unit = settings_before.get('context_unit', fn['unit'])
                context = net.store.one('SELECT header FROM context_versions WHERE project_id=? AND unit=? AND version=?',
                                        fn['project_id'], unit, before.get('context_version'))
                baseline = {'source':attempt['source'], 'source_sha256':before['source_sha256'],
                            'score':before.get('score'), 'settings':settings_before,
                            'header':context['header'] if context else None}
                # Optional research evidence is bounded independently of the successful match packet.
                if (len(json.dumps(baseline).encode()) <= 350000
                    and len(json.dumps(result | {'repair_baseline':baseline}).encode()) <= 1200000):
                    result['repair_baseline'] = baseline
    return result


def attach_baseline(net, fn, agent, source, evidence):
    """Keep client history separate from host-scored trials; never fabricate host attempts/scores."""
    if not isinstance(evidence, dict):
        return
    baseline = evidence.get('repair_baseline')
    if not isinstance(baseline, dict) or len(json.dumps(baseline).encode()) > 350000:
        return
    text = baseline.get('source')
    score = baseline.get('score')
    header = baseline.get('header')
    if (not isinstance(text, str) or not text.strip() or len(text.encode()) > 262144
        or not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 < score < 1e12
        or baseline.get('source_sha256') != match_bundle.digest(text)
        or not isinstance(baseline.get('settings'), dict)
        or header is not None and (not isinstance(header, str) or len(header.encode()) > source_preparation.MAX_HEADER)
        or evidence.get('source_sha256') != match_bundle.digest(source)
        or evidence.get('target_sha256') != match_bundle.digest(fn['target'] or b'')
        or evidence.get('vram') != fn['vram']):
        return
    with net.store.tx() as db:
        row = db.execute("SELECT id,evidence FROM repair_examples WHERE function_id=? AND agent_id=? AND json_extract(evidence,'$.after.source_sha256')=? ORDER BY id DESC LIMIT 1",
                         (fn['id'], agent.id, match_bundle.digest(source))).fetchone()
        if row:
            case = json.loads(row['evidence'])
            case['worker_baseline'] = {k:baseline.get(k) for k in ('source','source_sha256','score','settings','header')}
            case['worker_baseline']['verification'] = 'Worker-reported failed baseline; not rejudged by the host.'
            db.execute('UPDATE repair_examples SET evidence=? WHERE id=?', (json.dumps(case), row['id']))


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


def _pending_filter(net, slug, direction=None):
    prefix = PREFIX + str(net.project_row(slug)['id']) + ':'
    # LIKE's default case-insensitive comparison cannot use the binary key index.
    # Room keys end in an ASCII hash; the next delimiter bounds exactly this project.
    where = "key >= ? AND key < ? AND json_extract(value,'$.status')='pending'"
    args = [prefix, prefix[:-1] + ';']
    if direction is not None:
        where += " AND json_extract(value,'$.direction')=?"
        args.append(direction)
    return where, args


def pending(net,slug, direction=None):
    where, args = _pending_filter(net, slug, direction)
    return [dict(json.loads(row['value']), key=row['key']) for row in net.store.all(
        "SELECT key,value FROM settings WHERE " + where + " ORDER BY json_extract(value,'$.created_at')", *args)]


def public(net,slug):
    # The drawer needs metadata only, never candidate C, headers, or compiler evidence.
    where, args = _pending_filter(net, slug)
    fields = ('function','agent','direction','reason','updated_at')
    columns = ','.join("json_extract(value,'$." + field + "') AS " + field for field in fields)
    return [dict(row) for row in net.store.all("SELECT key," + columns + " FROM settings WHERE " + where
                                              + " ORDER BY json_extract(value,'$.created_at')", *args)]


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


def project_profiles(net, proj, fn):
    """Recover missing client settings from profiles actually assigned in this project."""
    cfg = net.cfg(proj)
    default = cfg.get('compiler') or {}
    candidates = [compiler_for(cfg, fn['vram'], fn), default]
    candidates += [{**default, **profile} for profile in cfg.get('compiler_ranges', [])]
    candidates += [{**default, **profile} for profile in (cfg.get('function_compilers') or {}).values()]
    profiles = []
    for candidate in candidates:
        profile = {k: candidate.get(k) for k in ('toolchain', 'flags')}
        if profile in profiles or not toolchains.installed(profile['toolchain']):
            continue
        profiles.append(settings(net, proj, profile))
    return profiles


def entry_function(net, proj, fn, item):
    """Old address-only mirroring could queue one overlay's body on another overlay's row.
    Recover only a named definition at the same address; the normal judge must prove its actual bytes."""
    from ..forks import csource
    from .function_names import _C_TOKEN

    evidence = item.get('evidence') or {}
    submitted = net.store.one('SELECT * FROM functions WHERE id=? AND project_id=?',
                             item.get('submitted_function_id', fn['id']), proj['id'])
    if (not submitted or evidence.get('source_sha256') != match_bundle.digest(item['source']) or
        evidence.get('target_sha256') != match_bundle.digest(submitted['target'] or b'') or
        evidence.get('vram') != submitted['vram']):
        return fn
    if csource.find(item['source'], fn['name']) is not None:
        return fn
    names = {token.group() for token in _C_TOKEN.finditer(item['source'])
             if re.fullmatch(r'func_[0-9A-Fa-f]{8}_(?:overlay_[0-9]{3}|ovl_[0-9]{2,3})', token.group())}
    rows = [row for row in net.store.all('SELECT * FROM functions WHERE project_id=? AND vram=?',
                                       proj['id'], submitted['vram'])
            if row['name'] in names and csource.find(item['source'], row['name']) is not None]
    if len(rows) == 1:
        item.setdefault('submitted_function_id', submitted['id'])
        item.setdefault('submitted_function', submitted['name'])
        item.update(function_id=rows[0]['id'], function=rows[0]['name'])
        return rows[0]
    return fn


def entry_source(fn, source, header):
    """Overlay numbering can change between imports. Rename only an unambiguous generated entry at this
    address; callers must validate the submitted target hash first and rejudge the rewritten source."""
    from ..forks import csource
    from .function_names import _C_TOKEN

    if csource.find(source, fn['name']) is not None:
        return source, header
    pattern = re.compile(r'func_([0-9A-Fa-f]{8})(?:_(?:overlay_[0-9]{3}|ovl_[0-9]{2,3}))?\Z')
    names = set()
    for token in _C_TOKEN.finditer(source):
        name = token.group()
        match = pattern.fullmatch(name)
        if match and int(match[1], 16) == fn['vram'] and csource.find(source, name) is not None:
            names.add(name)
    if len(names) != 1:
        raise NetworkError('Cannot identify one client entry at the host function address. Resubmit with its current function name.')
    old = names.pop()
    def rewrite(text):
        return _C_TOKEN.sub(lambda token: fn['name'] if token.group() == old else token.group(), text)
    return rewrite(source), rewrite(header)


def verify(net,slug,agent,function,source,evidence=None):
    key,item=save(net,slug,'host',agent,function,source,evidence)
    result=retry(net,slug,key,recover_profiles=bool(evidence))
    if not result['matched']:
        # The client finished its local exact candidate. A pending host review must not strand its next claim.
        with net.store.tx() as db:
            removed=db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(item['function_id'],agent.id)).rowcount
            if removed:net._event(db,net.project_row(slug)['id'],agent.id,'release',function)
    return result


def retry(net,slug,key, *, recover_profiles=True, progress=None):
    report=progress or (lambda value:None)
    report({'phase':'Loading saved submission'})
    proj=net.project_row(slug)
    if not key.startswith(PREFIX+str(proj['id'])+':'):
        raise NetworkError('Choose a submission belonging to this project.')
    row=net.store.one('SELECT value FROM settings WHERE key=?',key)
    if not row:raise NetworkError('Room submission is no longer available.')
    item=json.loads(row['value']);fn=net.store.one('SELECT * FROM functions WHERE id=? AND project_id=?',item['function_id'],proj['id'])
    item['failure_reason']=failure_reason(item)
    if not fn:raise NetworkError('Room submission function was removed.')
    fn=entry_function(net,proj,fn,item)
    agent_row=net.store.one('SELECT id,name,owner,trusted FROM agents WHERE id=?',item['agent_id'])
    if not agent_row:raise NetworkError('Room submission agent no longer exists.')
    agent=Agent(agent_row['id'],agent_row['name'],agent_row['owner'],bool(agent_row['trusted']))
    result={'matched':False,'score':None,'pending':True,'submission':key}
    verification_claim=False
    try:
        if fn['status']=='matched':
            result.update(matched=True,pending=False)
        else:
            # Recovered submissions share one attribution identity. Verification is
            # scoped to this candidate, not that identity's ongoing work elsewhere.
            report({'phase':'Checking function availability', 'function':fn['name']})
            with net.store.tx() as db:
                net._expire_claims(db)
                claim=db.execute('SELECT agent_id FROM claims WHERE function_id=?',(fn['id'],)).fetchone()
                if claim and claim['agent_id']!=agent.id:
                    raise NetworkError('This function is claimed by another worker; retry when released.')
                if not claim:
                    now=time.time()
                    db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],agent.id,now,now+net.claim_ttl))
                    verification_claim=True
            own=compiler_for(net.cfg(proj),fn['vram'], fn)
            # Portable submissions carry the exact compiler/header bundle. Verify it first;
            # an ordinary host attempt can use different declarations and obscure that evidence.
            if not item.get('evidence') and toolchains.installed(own['toolchain']):
                report({'phase':'Compiling and comparing with host settings', 'toolchain':own['toolchain'], 'flags':own['flags']})
                res=net.attempt(agent,fn['name'],item['source'],slug)
            else:
                res={'matched':False,'score':None}
            result.update(score=res['score'],matched=res['matched'])
            fn=net._function(fn['name'],slug);proj=net.project_row(slug)
            if not res['matched']:
                report({'phase':'Checking client evidence and preparing compiler profiles'})
                source=item['source']
                evidence=item.get('evidence')
                header=net._context(proj['id'],fn['unit'])['header']
                if evidence:
                    submitted=net.store.one('SELECT target FROM functions WHERE id=? AND project_id=?',
                                             item.get('submitted_function_id',fn['id']),proj['id'])
                    if (not submitted or evidence.get('source_sha256')!=match_bundle.digest(item['source']) or
                        evidence.get('target_sha256')!=match_bundle.digest(submitted['target'] or b'') or evidence.get('vram')!=fn['vram']):
                        raise NetworkError('Client source/target identity differs from the host. Resubmit from the same game/function.')
                    header=evidence.get('header')
                    if (not isinstance(header,str) or len(header.encode())>source_preparation.MAX_HEADER or
                        evidence.get('header_sha256',match_bundle.digest(header))!=match_bundle.digest(header)):
                        raise NetworkError('Invalid room header evidence.')
                    source,header=entry_source(fn,source,header)
                    profiles=([settings(net,proj,evidence['compiler'])] if evidence.get('compiler')
                              else project_profiles(net,proj,fn))
                    current={k:own[k] for k in ('toolchain','flags')}
                    if current not in profiles and toolchains.installed(current['toolchain']):
                        # The host's profile may reproduce the same bytes with the client's private declarations.
                        profiles.append(current)
                else:
                    profiles=project_profiles(net,proj,fn)
                    if recover_profiles:
                        # Explicit recovery may continue beyond the project's assigned profiles.
                        for tc, flags in net._comparison_candidates(proj,all_installed=True):
                            profile={'toolchain':tc,'flags':list(flags)}
                            if profile not in profiles:profiles.append(profile)
                project_count=len(project_profiles(net,proj,fn)) if not evidence or not evidence.get('compiler') else 0
                budget=max(6,project_count)
                identities={p['toolchain']:match_bundle.compiler_identity(p) for p in profiles}
                scope=hashlib.sha256((candidate_scope.key(net,proj,fn)+json.dumps(identities,sort_keys=True)+json.dumps(profiles,sort_keys=True)+build_info.commit()).encode()).hexdigest()
                if item.get('scope')!=scope:item.update(scope=scope,tried=[])
                tested=0
                remaining=[p for p in profiles if json.dumps(p,sort_keys=True) not in item['tried']][:budget]
                for profile in profiles:
                    marker=json.dumps(profile,sort_keys=True)
                    if marker in item['tried']:continue
                    if tested>=budget:break
                    tested+=1
                    report({'phase':'Rechecking source, header and linked bytes', 'profile_index':tested,
                            'profile_total':len(remaining), 'toolchain':profile['toolchain'], 'flags':profile['flags'],
                            'profile_source':'Client settings + client header' if evidence and evidence.get('compiler') and profile==profiles[0] else
                                             'Host fallback + client header' if evidence else 'Host recovery profile (client settings unavailable)'})
                    try:
                        expected=source_preparation.inputs(net,net.project_row(slug),net._function(fn['name'],slug))
                        prepared_evidence.promote_measured(net,net.project_row(slug),net._function(fn['name'],slug),
                                                          source,header,profile,agent,expected)
                        result.update(matched=True,score=0,pending=False,compiler=profile)
                        break
                    except NetworkError as error:
                        item['failure_reason']=str(error)
                        if not any(word in str(error).lower() for word in ('claim','changed','expired','busy')):
                            item['tried'].append(marker)
                if not result['matched']:
                    item['reason']=item['failure_reason']+f" Tried {len(item['tried'])}/{len(profiles)} profiles in this context; evidence retained."
    except (NetworkError,ValueError,toolchains.ToolchainError) as error:
        item['reason']=item['failure_reason']=str(error)
    finally:
        if verification_claim:
            with net.store.tx() as db:
                db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?',(fn['id'],agent.id))
    report({'phase':'Saving verification result'})
    if result['matched']:
        attach_baseline(net,fn,agent,item['source'],item.get('evidence'))
        item.update(status='accepted',reason='Host verified exact bytes',source='',evidence=None,tried=[])
        result['pending']=False
    update(net,key,item)
    result['reason']=item['reason']
    return result


def send(link,agent,function,source, *, evidence=None):
    net,slug=link.net,link.slug
    if evidence is None:evidence=packet(net,slug,function,source)
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
    accepted={(r.get('name'),r['vram']) for r in state.get('matched',[])}
    agent,_=link.net.local_agent('saved-progress')
    proj=link.net.project_row(link.slug)
    for fn in link.net.store.all("SELECT name,vram,matched_source FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL",proj['id']):
        if (fn['name'],fn['vram']) not in accepted:
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
            cfg['function_compilers']=profiles.get('function_compilers',{})
            db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
        for item in contexts:
            if item.get('rom_offset') is not None:
                fn=db.execute('SELECT * FROM functions WHERE project_id=? AND vram=? AND rom_offset=?',
                               (proj['id'],item['vram'],item['rom_offset'])).fetchone()
            else:
                rows=db.execute('SELECT * FROM functions WHERE project_id=? AND vram=?',(proj['id'],item['vram'])).fetchall()
                fn=rows[0] if len(rows)==1 else None
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
