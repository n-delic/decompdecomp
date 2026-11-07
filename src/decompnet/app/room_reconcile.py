"""Host-requested inventories and fresh client bundles; only host verification grants a match."""
import hashlib
import json
import threading
import time
import uuid

from ..network import NetworkError
from . import room_matches

PAGE = 128


def _key(room):
    return f'room-reconcile:{room.proj["id"]}'


def snapshot(room):
    row = room.store.one('SELECT value FROM settings WHERE key=?', _key(room))
    return json.loads(row['value']) if row else None


def _save(room, state):
    with room.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (_key(room), json.dumps(state)))


def lock(room):
    return room.__dict__.setdefault('_reconcile_lock', threading.RLock())


def start(room):
    if room.fork:
        raise NetworkError('Room reconciliation currently supports ROM projects; imported builds require their source-tree verification.')
    with lock(room):
        previous = snapshot(room)
        if previous and previous['active']:
            return previous
        members = room.net.store.all('SELECT name,last_seen FROM agents WHERE origin=? AND owner IS NULL', room.origin)
        state = {'id':uuid.uuid4().hex, 'active':True, 'started':time.time(), 'members':{}, 'results':{}}
        capabilities = room.__dict__.get('_reconcile_capabilities', {})
        for member in members:
            online = member['last_seen'] and time.time()-member['last_seen'] < 30
            supported = time.time()-capabilities.get(member['name'], 0) < 30
            status = 'waiting' if online and supported else 'offline' if not online else 'update_required'
            state['members'][member['name']] = {'status':status, 'checked':0, 'shared':0, 'pages':[], 'total':None}
        _save(room, state)
        return state


def command(room, member, capable=False):
    if capable:
        room.__dict__.setdefault('_reconcile_capabilities', {})[member.name] = time.time()
    state = snapshot(room)
    entry = (state or {}).get('members', {}).get(member.name)
    if state and state['active'] and entry and entry['status'] in ('waiting', 'running'):
        return {'id':state['id']}
    return None


def _current(room, member, run):
    state = snapshot(room)
    if not state or not state['active'] or state['id'] != run or member.name not in state['members']:
        raise NetworkError('This room reconciliation ended. Start a new reconciliation from the host.')
    return state, state['members'][member.name]


def _identity(item):
    return hashlib.sha256(json.dumps([item.get(k) for k in ('name','vram','rom_offset','size','target_sha256')]).encode()).hexdigest()


def exchange(room, member, data):
    """Authenticated members can inventory their own matches and supply only requested bundles."""
    action = data.get('action')
    with lock(room):
        state, entry = _current(room, member, data.get('id'))
        if action == 'finish':
            entry.update(status='interrupted' if data.get('error') else 'done', error=str(data.get('error') or '')[:1000])
            if entry['total'] is None and not data.get('error'):entry['total']=0
            _save(room, state)
            return {'ok':True}
        if action == 'inventory':
            items, page, total = data.get('items'), data.get('page'), data.get('total')
            if (not isinstance(items,list) or len(items)>PAGE or not isinstance(page,int) or page<0
                    or not isinstance(total,int) or total<0 or total>1000000):
                raise NetworkError('Send a bounded match inventory page and its total.')
            requested=[]
            for item in items:
                if (not isinstance(item,dict) or not isinstance(item.get('name'),str) or len(item['name'])>256
                        or not isinstance(item.get('vram'),int) or not isinstance(item.get('size'),int)
                        or not isinstance(item.get('target_sha256'),str) or len(item['target_sha256'])!=64):
                    raise NetworkError('Match inventory needs a function name, location, size and target hash.')
                rows = room.store.all('SELECT id,name,status,target FROM functions WHERE project_id=? AND vram=? AND size=? '
                                      'AND rom_offset IS ?', room.proj['id'], item['vram'], item['size'], item.get('rom_offset'))
                from ..match_bundle import digest
                same = [r for r in rows if digest(r['target'] or b'') == item['target_sha256']]
                ident = member.name+':'+_identity(item)
                if len(same)==1 and same[0]['status']=='matched':
                    if page not in entry['pages']:entry['shared']+=1
                    continue
                function = same[0]['name'] if len(same)==1 else None
                row = state['results'].setdefault(ident, {'client':member.name, 'function':item['name'], 'host_function':function,
                    'target_sha256':item['target_sha256'], 'status':'waiting' if function else 'not_applied',
                    'reason':'Waiting for fresh client bundle' if function else 'Host layout or target differs; no unambiguous function at this ROM location.'})
                if function and row['status']=='waiting':requested.append({'key':ident, 'local':item['name'], 'function':function})
            if page not in entry['pages']:
                entry['pages'].append(page)
                entry['checked']+=len(items)
            entry.update(status='running',total=total,updated=time.time())
            _save(room,state)
            return {'requested':requested}
        if action not in ('apply','skip'):raise NetworkError('Choose inventory, apply, skip or finish.')
        key = data.get('key')
        row = state['results'].get(key)
        if not row or row['client']!=member.name or not row['host_function']:
            raise NetworkError('This bundle was not requested from this room member.')
        if row['status']!='waiting':return {'matched':row['status']=='applied', 'reason':row['reason']}
        if action=='skip':
            row.update(status='not_applied',reason='Client could not supply its bundle: '+str(data.get('reason') or 'Inputs changed; reconcile again.')[:1000])
            _save(room,state)
            return {'matched':False,'reason':row['reason']}
        evidence=data.get('evidence')
        if not isinstance(evidence,dict) or evidence.get('target_sha256')!=row['target_sha256']:
            raise NetworkError('The fresh bundle target differs from the inventoried function. Reconcile again.')
        function=row['host_function']
        row.update(status='verifying',reason='Host compiling client source, header and compiler settings')
        entry['updated']=time.time()
        _save(room,state)
    try:
        agent=room.agent_for(member,'room-reconcile')
        result=room_matches.verify(room.net,room.slug,agent,function,data.get('source'),evidence,preserve_exact=True)
    except (NetworkError,ValueError) as error:
        result={'matched':False,'reason':str(error)}
    with lock(room):
        current=snapshot(room)
        if current and current['id']==data.get('id'):
            current['results'][key].update(status='applied' if result.get('matched') else 'not_applied',
                                           reason=result.get('reason') or 'Host could not verify this bundle')
            current['members'][member.name]['updated']=time.time()
            _save(room,current)
    return result


def close(room):
    with lock(room):
        state=snapshot(room)
        if state:
            state['active']=False
            for row in state['results'].values():
                if row['status'] in ('waiting','verifying'):
                    row.update(status='not_applied',reason='Reconciliation interrupted. Run again to resume; an in-flight host check may still finish.')
            for entry in state['members'].values():
                if entry['status'] in ('running','waiting'):entry['status']='interrupted'
            _save(room,state)


def run(room, log):
    state=start(room)
    settle_pending(room)
    try:
        while True:
            log.check_cancel()
            with lock(room):
                state=snapshot(room)
                for entry in state['members'].values():
                    if entry['status']=='waiting' and time.time()-state['started']>45:entry['status']='unresponsive'
                    if entry['status']=='running' and time.time()-entry.get('updated',state['started'])>180:entry['status']='interrupted'
                _save(room,state)
            rows=list(state['results'].values())
            log.progress({'phase':'Reconciling room matches', 'finished':sum(r['status'] in ('applied','not_applied') for r in rows),
                          'total':len(rows), 'members':state['members'], 'results':rows})
            if not any(m['status'] in ('waiting','running') for m in state['members'].values()):break
            log.cancel_event.wait(0.5)
        log(f"Reconciliation complete: {sum(r['status']=='applied' for r in rows)} matches applied; "
            f"{sum(r['status']=='not_applied' for r in rows)} need attention.")
        return state | {'active':False}
    finally:
        close(room)
        settle_pending(room)


def launch(link, command):
    """Heartbeat dispatch stays independent of slow room mirroring and compilation."""
    if not command or link.fork:return
    with link.__dict__.setdefault('_reconcile_lock',threading.Lock()):
        worker=link.__dict__.get('_reconcile_worker')
        if worker and worker.is_alive():return
        if link.__dict__.get('_reconcile_done')==command['id']:return
        def work():
            try:
                client_run(link,command['id'])
            finally:
                # Failures remain visible on the host; another deliberate run uses a new ID.
                link._reconcile_done=command['id']
        thread=threading.Thread(target=work,daemon=True,name='room-reconcile-'+link.slug)
        link._reconcile_worker=thread
        thread.start()


def client_run(link, run):
    net=link.net
    proj=net.project_row(link.slug)
    from ..match_bundle import digest
    rows=net.project_store(link.slug).all("SELECT name,vram,rom_offset,size,target FROM functions WHERE project_id=? "
                                         "AND status='matched' AND matched_source IS NOT NULL ORDER BY id",proj['id'])
    error=''
    try:
        for offset in range(0,len(rows),PAGE):
            if link._stop.is_set():raise NetworkError('Client disconnected during reconciliation; run it again after reconnecting.')
            page=[{k:r[k] for k in ('name','vram','rom_offset','size')} | {'target_sha256':digest(r['target'] or b'')} for r in rows[offset:offset+PAGE]]
            response=link._req('POST','/collab/reconcile',{'id':run,'action':'inventory','page':offset//PAGE,'total':len(rows),'items':page})
            for item in response['requested']:
                try:
                    fn=net._function(item['local'],link.slug)
                    source=fn['matched_source']
                    if fn['status']!='matched' or not source:
                        raise NetworkError('Client inputs changed; reconcile again.')
                    evidence=room_matches.packet(net,link.slug,fn['name'],source)
                except NetworkError as exc:
                    link._req('POST','/collab/reconcile',{'id':run,'action':'skip','key':item['key'],'reason':str(exc)})
                    continue
                link._req('POST','/collab/reconcile',{'id':run,'action':'apply','key':item['key'],'source':source,'evidence':evidence})
    except Exception as exc:
        error=str(exc)
    try:
        link._req('POST','/collab/reconcile',{'id':run,'action':'finish','error':error})
    except NetworkError:
        # Host keeps the last inventory/results, including which function was in flight.
        pass


def settle_pending(room):
    """Retire stale submissions only where the host already holds an exact match."""
    prefix=room_matches.PREFIX+str(room.proj['id'])+':'
    rows=room.store.all("SELECT key,json_extract(value,'$.function_id') AS fid FROM settings "
                        "WHERE key>=? AND key<? AND json_extract(value,'$.status')='pending'",prefix,prefix[:-1]+';')
    with room.store.tx() as db:
        for row in rows:
            fn=db.execute("SELECT status FROM functions WHERE id=? AND project_id=?",(row['fid'],room.proj['id'])).fetchone()
            if fn and fn['status']=='matched':
                db.execute("UPDATE settings SET value=json_set(value,'$.status','accepted','$.reason',"
                           "'Host already holds an exact match','$.source','','$.evidence',NULL,'$.updated_at',?) WHERE key=?",(time.time(),row['key']))
