"""Room profiles/headers are evidence, never a substitute for host byte verification."""
import json

import pytest

from decompnet.app import collab, room_matches
from decompnet.app.engine import RomNetwork
from decompnet.project import compiler_for
from tests.test_app_engine import DAMAGE
from tests.test_rom import built  # noqa: F401


@pytest.fixture
def pair(built,tmp_path):  # noqa: F811
    host=RomNetwork(tmp_path/'host.db',tmp_path/'host')
    slug=host.import_rom(built,confirmed=True,log=lambda *_:None)
    guest=RomNetwork(tmp_path/'guest.db',tmp_path/'guest')
    gslug=guest.import_rom(built,confirmed=True,log=lambda *_:None)
    return host,slug,guest,gslug


def test_header_and_function_profile_travel_and_host_rechecks(pair,monkeypatch):
    host,slug,guest,gslug=pair
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')
    proj=guest.project_row(gslug)
    with guest.store.tx() as db:
        db.execute("UPDATE context_versions SET header=header || '\n#define ROOM_TIMER 60\n' WHERE project_id=?",(proj['id'],))
    agent,_=guest.local_agent('client')
    assert guest.attempt(agent,'func_80000808',source,gslug)['matched']
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    assert 'ROOM_TIMER 60' in evidence['header'] and evidence['compiler_identity']
    original=host.cfg(host.project_row(slug))
    changed=json.loads(json.dumps(original));changed['compiler']['flags']=['-O0']
    with host.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE slug=?',(json.dumps(changed),slug))
    remote,_=host.local_agent('room-client')
    monkeypatch.setattr(host,'attempt',lambda *args:pytest.fail('portable bundle must use client settings before any host attempt'))
    result=room_matches.verify(host,slug,remote,'func_80000808',source,evidence)
    assert result['compiler']=={k:evidence['compiler'][k] for k in ('toolchain','flags')}
    assert result['matched'],result.get('reason')
    fn=host._function('func_80000808',slug);cfg=host.cfg(host.project_row(slug))
    assert cfg['compiler']==changed['compiler']  # Default and neighbours do not adopt the client's profile.
    assert compiler_for(cfg,fn['vram'],fn)['flags']==evidence['compiler']['flags']
    assert host._context(fn['project_id'],fn['unit'])['header']==evidence['header']
    assert not room_matches.pending(host,slug)
    assert host.store.one('SELECT header FROM verified_matches WHERE function_id=?',fn['id'])['header']==evidence['header']
    room=collab.Room(host,slug)
    state=room.state(0,0,0,remote,fresh=True)
    link=collab.Link.__new__(collab.Link)
    link.net=guest;link.slug=gslug;link.member='client';link.fork=False;link.cursor={'e':0,'a':0,'v':0};link.members=[];link.claims=[]
    # Apply just the authoritative inputs; the ordinary room tests cover complete state mirroring.
    room_matches.sync_inputs(guest,gslug,state)
    gfn=guest._function('func_80000808',gslug)
    assert guest._context(gfn['project_id'],gfn['unit'])['header']==evidence['header']
    assert compiler_for(guest.cfg(guest.project_row(gslug)),gfn['vram'],gfn)['flags']==evidence['compiler']['flags']


def test_mismatch_retained_and_missing_compiler_can_be_retried(pair,monkeypatch):
    host,slug,guest,gslug=pair
    agent,_=host.local_agent('room-client')
    # Deliberately needs a private macro so the host's ordinary context cannot accept it first.
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    evidence['header']+='\n#define ROOM_TIMER 60\n'
    evidence['header_sha256']=room_matches.match_bundle.digest(evidence['header'])
    from decompnet import toolchains
    installed=toolchains.installed
    monkeypatch.setattr(toolchains,'installed',lambda tid:None)
    result=room_matches.verify(host,slug,agent,'func_80000808',source,evidence)
    assert not result['matched'] and result['pending'] and 'installed' in result['reason']
    assert not host.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)
    saved=room_matches.pending(host,slug)[0]
    assert saved['source']==source and saved['evidence']['header']==evidence['header']
    monkeypatch.setattr(toolchains,'installed',installed)
    result=room_matches.retry(host,slug,saved['key'])
    assert result['matched'],result.get('reason')


def test_invalid_evidence_never_changes_settings(pair):
    host,slug,guest,gslug=pair
    agent,_=host.local_agent('room-client')
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    evidence.update(target_sha256='wrong',header='private header')
    before=host.project_row(slug)['config']
    result=room_matches.verify(host,slug,agent,'func_80000808',source,evidence)
    assert result['pending'] and 'identity differs' in result['reason']
    assert host.project_row(slug)['config']==before
    assert host._function('func_80000808',slug)['status']!='matched'


def test_legacy_candidates_walk_installed_profiles_with_a_budget(pair,monkeypatch):
    host,slug,_,_=pair
    agent,_=host.local_agent('room-client')
    monkeypatch.setattr(host,'attempt',lambda *args:{'matched':False,'score':100})
    monkeypatch.setattr(room_matches.build_info,'commit',lambda:'build-one')
    monkeypatch.setattr(room_matches,'project_profiles',lambda *args:[])
    profiles=[('gcc-2.7.2-kmc',(f'-O{i}',)) for i in range(10)]
    monkeypatch.setattr(host,'_comparison_candidates',lambda *args,**kwargs:profiles)
    seen=[]
    def miss(*args):
        from decompnet.network import NetworkError
        seen.append(args[5]);raise NetworkError('Linked bytes differ')
    monkeypatch.setattr(room_matches.prepared_evidence,'promote_measured',miss)
    result=room_matches.verify(host,slug,agent,'func_80000808',DAMAGE)
    assert result['pending'] and len(seen)==0
    updates=[]
    room_matches.retry(host,slug,result['submission'],progress=updates.append)
    trials=[p for p in updates if p.get('profile_index')]
    assert [p['profile_index'] for p in trials]==list(range(1,7))
    assert all(p['profile_total']==6 and p['toolchain']=='gcc-2.7.2-kmc' for p in trials)
    assert len(seen)==6
    assert room_matches.pending(host,slug)  # Six trials do not exhaust the ten-profile search.
    room_matches.retry(host,slug,result['submission'])
    assert len(seen)==10
    before=room_matches.retry(host,slug,result['submission'])['reason']
    after=room_matches.retry(host,slug,result['submission'])['reason']
    assert len(seen)==10 and before==after and after.count('Tried ')==1
    monkeypatch.setattr(room_matches.build_info,'commit',lambda:'build-two')
    room_matches.retry(host,slug,result['submission'])
    assert len(seen)==16  # a verifier update must not inherit the old failed-profile cache


def test_old_repeated_pending_explanations_are_reduced_to_the_actual_failure():
    reason=('Host has not verified this source/header/profile. '*4+'Unknown symbol. '
            +'Tried 1/1 profiles in this context. Evidence retained. '*3)
    assert room_matches.failure_reason({'reason':reason})=='Unknown symbol.'


@pytest.mark.parametrize('value',[60,61])
def test_room_bundle_isolates_entry_but_still_requires_exact_original_bytes(pair,value):
    from decompnet.forks import csource
    host,slug,guest,gslug=pair
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')+'\nint unrelated(void) { return 7; }\n'
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    evidence['header']+=f'\n#define ROOM_TIMER {value}\n'
    evidence['header_sha256']=room_matches.match_bundle.digest(evidence['header'])
    remote,_=host.local_agent('room-client')
    result=room_matches.verify(host,slug,remote,'func_80000808',source,evidence)
    assert result['matched']==(value==60),result['reason']
    fn=host._function('func_80000808',slug)
    if value==60:
        assert csource.find(fn['matched_source'],'unrelated') is None
        assert csource.get(fn['matched_source'],'func_80000808')==csource.get(source,'func_80000808')
    else:
        assert fn['status']=='open' and result['rejected']
        saved=json.loads(host.store.one('SELECT value FROM settings WHERE key=?',result['submission'])['value'])
        assert saved['source']==source and not room_matches.pending(host,slug)


def test_saved_match_profile_is_preserved_across_join_and_host_sync(pair,built):  # noqa: F811
    from starlette.testclient import TestClient
    host,slug,guest,gslug=pair
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')
    with guest.store.tx() as db:
        db.execute("UPDATE context_versions SET header=header || '\n#define ROOM_TIMER 60\n'")
    agent,_=guest.local_agent('client')
    assert guest.attempt(agent,'func_80000808',source,gslug)['matched']
    successful=room_matches.packet(guest,gslug,'func_80000808',source)
    cfg=json.loads(host.project_row(slug)['config']);cfg['compiler']['flags']=['-O0']
    with host.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    room=host.rooms[slug]=collab.Room(host,slug);invite=room.open_invite()
    client=TestClient(collab.host_app(host,host.rooms),base_url='https://room.example')
    collab.join_room(guest,f'https://room.example/join/{invite}#room={slug}','client',built,
                     log=lambda *_:None,client=client,start=False)
    assert host._function('func_80000808',slug)['status']=='matched'
    guest.links[gslug].sync()
    fn=guest._function('func_80000808',gslug)
    assert compiler_for(guest.cfg(guest.project_row(gslug)),fn['vram'],fn)['flags']==successful['compiler']['flags']
    assert guest._context(fn['project_id'],fn['unit'])['header']==successful['header']


def test_resend_retains_original_evidence_after_local_settings_change(pair):
    from types import SimpleNamespace
    _,_,guest,slug=pair
    agent,_=guest.local_agent('client')
    evidence=room_matches.packet(guest,slug,'func_80000808',DAMAGE)
    key,_=room_matches.save(guest,slug,'client',agent,'func_80000808',DAMAGE,evidence,'https://room.example/test')
    cfg=json.loads(guest.project_row(slug)['config']);cfg['compiler']['flags']=['-O0']
    with guest.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    seen=[]
    link=SimpleNamespace(net=guest,slug=slug,url='https://room.example',room='test',
                         _req=lambda method,path,data: seen.append(data) or {'matched':True})
    room_matches.resend(link,key)
    assert seen[0]['evidence']==evidence
    assert not room_matches.pending(guest,slug)


def test_pending_match_review_routes_are_guarded_and_project_scoped(pair,tmp_path,monkeypatch):
    import time

    from starlette.testclient import TestClient

    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW

    host,slug,_,_=pair
    a,_=host.local_agent('remote')
    key,_=room_matches.save(host,slug,'host',a,'func_80000808',DAMAGE,None)
    home=tmp_path/'.decomp';home.mkdir()
    mcp,_,security,_=build(host,home,PORT,SECRET)
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    url='/api/collab/matches'
    assert client.get(url+'?slug='+slug,headers={'User-Agent':'outside'}).status_code==401
    data=client.get(url+'?slug='+slug).json()
    assert data['rows'][0]['key']==key and 'source' not in data['rows'][0]
    assert client.post(url,json={'slug':slug,'key':key}).status_code==403
    assert client.post(url,json={'slug':slug,'key':'room-match:999:other'},headers={'X-Decomp':'1'}).status_code==400
    called=[]
    monkeypatch.setattr(room_matches,'retry',lambda *args, **kwargs: called.append(args[-1]) or {'matched':True,'reason':'Host confirmed'})
    job=client.post(url,json={'slug':slug,'key':key},headers={'X-Decomp':'1'}).json()['job']
    for _ in range(100):
        state=client.get('/api/job?id='+job).json()
        if state['state']!='running':break
        time.sleep(.01)
    assert state['state']=='done' and called==[key]


def test_older_host_checked_room_attempts_can_be_recovered(pair):
    host,slug,_,_=pair
    agent,_=host.local_agent('old-client')
    with host.store.tx() as db:db.execute('UPDATE agents SET origin=? WHERE id=?',('collab:'+slug,agent.id))
    # Historical server-side records are the evidence that an old room client submitted this C.
    fn=host._function('func_80000808',slug)
    host._record(fn,agent,10,DAMAGE,host._context(fn['project_id'])['version'])
    assert room_matches.recover_legacy(host,slug)==1
    assert room_matches.pending(host,slug)[0]['source']==DAMAGE


def test_room_profiles_reject_command_and_plugin_injection(pair):
    from decompnet.network import NetworkError
    host,slug,_,_=pair
    with pytest.raises(NetworkError,match='Unsupported'):
        room_matches.settings(host,host.project_row(slug),{'toolchain':'gcc-2.7.2-kmc','flags':[],'cmd':['evil']})
    with pytest.raises(NetworkError,match='flags not offered'):
        room_matches.settings(host,host.project_row(slug),{'toolchain':'gcc-2.7.2-kmc','flags':['-fplugin=/tmp/evil.so']})


def test_worker_failed_baseline_travels_without_becoming_host_trial(pair):
    host,slug,guest,gslug=pair
    worker,_=guest.local_agent('client')
    bad=DAMAGE.replace('= 60;', '= 61;')
    assert not guest.attempt(worker,'func_80000808',bad,gslug)['matched']
    assert guest.attempt(worker,'func_80000808',DAMAGE,gslug)['matched']
    evidence=room_matches.packet(guest,gslug,'func_80000808',DAMAGE)
    baseline=evidence['repair_baseline']
    assert baseline['source']==bad and baseline['score']>0 and baseline['header'] is not None
    remote,_=host.local_agent('room-worker')
    result=room_matches.verify(host,slug,remote,'func_80000808',DAMAGE,evidence)
    assert result['matched']
    fn=host._function('func_80000808',slug)
    row=host.store.one('SELECT id FROM repair_examples WHERE function_id=?',fn['id'])
    detail=host.repair_feedback.detail(slug,row['id'])['evidence']
    assert detail['worker_baseline']['source']==bad
    assert 'not rejudged' in detail['worker_baseline']['verification']
    assert 'worker-before.c' in detail['worker_diff']
    summary=host.repair_feedback.detail(slug,row['id'],include_source=False)['evidence']
    assert 'source' not in summary['worker_baseline'] and 'header' not in summary['worker_baseline']
    assert not host.store.one('SELECT 1 FROM attempts WHERE function_id=? AND source=?',fn['id'],bad)
    # Invalid advisory history cannot invalidate an otherwise host-verified exact candidate.
    evidence['repair_baseline']['source_sha256']='wrong'
    with host.store.tx() as db:
        case=db.execute('SELECT evidence FROM repair_examples WHERE id=?',(row['id'],)).fetchone()
        saved=json.loads(case['evidence']);saved.pop('worker_baseline')
        db.execute('UPDATE repair_examples SET evidence=? WHERE id=?',(json.dumps(saved),row['id']))
    room_matches.attach_baseline(host,fn,remote,DAMAGE,evidence)
    assert 'worker_baseline' not in host.repair_feedback.detail(slug,row['id'])['evidence']


def test_missing_client_compiler_uses_project_profile_with_private_header(pair,monkeypatch):
    host,slug,guest,gslug=pair
    source=DAMAGE.replace('= 60;', '= ROOM_TIMER;')
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    evidence.pop('compiler')
    evidence['header']+='\n#define ROOM_TIMER 60\n'
    evidence['header_sha256']=room_matches.match_bundle.digest(evidence['header'])
    remote,_=host.local_agent('room-client')
    result=room_matches.verify(host,slug,remote,'func_80000808',source,evidence)
    assert result['matched'],result['reason']


def test_project_profiles_include_default_ranges_and_function_overrides(monkeypatch):
    from types import SimpleNamespace
    default={'toolchain':'compiler','flags':['-O2']}
    cfg={'compiler':default,'compiler_ranges':[{'start':0,'end':8,'flags':['-O0']}],
         'function_compilers':{'16:32':{'vram':32,'toolchain':'compiler','flags':['-O1']}}}
    net=SimpleNamespace(cfg=lambda proj:cfg)
    monkeypatch.setattr(room_matches.toolchains,'installed',lambda tid:True)
    monkeypatch.setattr(room_matches,'settings',lambda net,proj,profile:profile)
    assert room_matches.project_profiles(net,{}, {'vram':64})==[
        default,{'toolchain':'compiler','flags':['-O0']},{'toolchain':'compiler','flags':['-O1']}]


def test_missing_compiler_tries_every_project_profile_automatically(pair,monkeypatch):
    host,slug,_,_=pair
    agent,_=host.local_agent('room-client')
    profiles=[{'toolchain':'gcc-2.7.2-kmc','flags':[f'-O{i}']} for i in range(8)]
    monkeypatch.setattr(room_matches,'project_profiles',lambda *args:profiles)
    monkeypatch.setattr(host,'attempt',lambda *args:{'matched':False,'score':100})
    seen=[]
    def miss(*args):
        from decompnet.network import NetworkError
        seen.append(args[5])
        raise NetworkError('Linked bytes differ')
    monkeypatch.setattr(room_matches.prepared_evidence,'promote_measured',miss)
    result=room_matches.verify(host,slug,agent,'func_80000808',DAMAGE)
    assert result['rejected'] and not result['pending'] and seen==profiles


def test_room_rechecks_saved_entry_with_different_overlay_name(pair):
    host,slug,guest,gslug=pair
    source=DAMAGE.replace('func_80000808','func_80000808_overlay_024')
    evidence=room_matches.packet(guest,gslug,'func_80000808',source)
    agent,_=host.local_agent('room-client')
    result=room_matches.verify(host,slug,agent,'func_80000808',source,evidence)
    assert result['matched'],result['reason']
    fn=host._function('func_80000808',slug)
    assert 'func_80000808_overlay_024' not in fn['matched_source']


def test_room_entry_rename_preserves_literals_and_other_overlay_symbols():
    fn={'name':'func_80100000_overlay_011','vram':0x80100000}
    source='void func_80100000_overlay_024(void) { func_80100004_overlay_024(); } // func_80100000_overlay_024\n'
    header='void func_80100000_overlay_024(void);\nchar *name="func_80100000_overlay_024";'
    rewritten, declarations=room_matches.entry_source(fn,source,header)
    assert rewritten.startswith('void func_80100000_overlay_011(void)')
    assert 'func_80100004_overlay_024();' in rewritten
    assert '// func_80100000_overlay_024' in rewritten
    assert '"func_80100000_overlay_024"' in declarations
    assert declarations.startswith('void func_80100000_overlay_011(void);')


@pytest.mark.parametrize('source',[
    'void func_80100004_overlay_024(void) {}',
    'void func_80100000_overlay_024(void) {} void func_80100000_overlay_018(void) {}',
])
def test_room_entry_rename_rejects_missing_or_ambiguous_identity(source):
    from decompnet.network import NetworkError
    with pytest.raises(NetworkError,match='Cannot identify one client entry'):
        room_matches.entry_source({'name':'func_80100000_overlay_011','vram':0x80100000},source,'')


@pytest.mark.parametrize('changed',[False,True])
def test_room_recovers_body_queued_on_wrong_overlay_only_after_exact_judgment(pair,changed):
    host,slug,_,_=pair
    fn=host._function('func_80000808',slug)
    name='func_80000808_overlay_024'
    columns=[r['name'] for r in host.store.all('PRAGMA table_info(functions)') if r['name']!='id']
    with host.store.tx() as db:
        db.execute('INSERT INTO functions ('+','.join(columns)+') SELECT '+
                   ','.join('?' if col=='name' else col for col in columns)+' FROM functions WHERE id=?',(name,fn['id']))
        db.execute('UPDATE functions SET target=? WHERE id=?',(bytes(len(fn['target'])),fn['id']))
    source=DAMAGE.replace('func_80000808',name)
    if changed:source=source.replace('= 60;','= 61;')
    evidence=room_matches.packet(host,slug,'func_80000808',source)
    agent,_=host.local_agent('room-client')
    result=room_matches.verify(host,slug,agent,'func_80000808',source,evidence)
    assert result['matched'] is (not changed),result['reason']
    assert host._function('func_80000808',slug)['status']=='open'
    assert (host._function(name,slug)['status']=='matched') is (not changed)


def test_deep_profile_probe_sends_exact_bundle_to_real_host_judge(pair):
    from types import SimpleNamespace

    from decompnet.app import deep_permuter, deep_profiles
    from tests.test_deep_permuter import Log
    host,slug,guest,gslug=pair
    name='func_80000808'
    profile={k:v for k,v in json.loads(json.dumps(guest.cfg(guest.project_row(gslug))['compiler'])).items() if k in ('toolchain','flags')}
    for net,project in ((host,slug),(guest,gslug)):
        cfg=net.cfg(net.project_row(project));cfg['compiler']['flags']=['-O0']
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE slug=?',(json.dumps(cfg),project))
    source=DAMAGE.replace('= 60;','= ROOM_TIMER;')
    proj=guest.project_row(gslug)
    with guest.store.tx() as db:
        db.execute("UPDATE context_versions SET header=header || '\n#define ROOM_TIMER 60\n' WHERE project_id=?",(proj['id'],))
    agent,_=guest.local_agent('deep-client');remote,_=host.local_agent('remote-deep-client')
    packets=[];synced=[]
    def request(method,path,data):
        assert method=='POST' and path=='/collab/attempt'
        packets.append(data)
        return room_matches.verify(host,slug,remote,data['function'],data['source'],data['evidence'])
    link=SimpleNamespace(net=guest,slug=gslug,url='http://host',room='room',_req=request,sync=lambda:synced.append(True))
    guest.links[gslug]=link
    fn=guest._function(name,gslug)
    search=deep_permuter.Search(guest,1,10,Log(),fn)
    assert deep_profiles.probe(search,agent,proj,fn,source,profile)==0,search.log.lines
    assert synced==[True] and packets[0]['evidence']['compiler']==profile
    hfn=host._function(name,slug)
    assert hfn['status']=='matched'
    assigned=compiler_for(host.cfg(host.project_row(slug)),hfn['vram'],hfn)
    assert {k:assigned[k] for k in ('toolchain','flags')}==profile
    assert 'ROOM_TIMER 60' in host._context(hfn['project_id'],hfn['unit'])['header']
    assert host.cfg(host.project_row(slug))['compiler']['flags']==['-O0']
    assert guest.cfg(guest.project_row(gslug))['compiler']['flags']==['-O0']


def test_offline_deep_search_verifies_locally_then_delivers_exact_match(pair):
    from types import SimpleNamespace

    from decompnet.app import deep_permuter, deep_room
    from tests.test_deep_permuter import Log
    host,slug,guest,gslug=pair
    agent,_=guest.local_agent('offline-deep');remote,_=host.local_agent('reconnected-deep')
    requests=[]
    def request(method,path,data):
        requests.append(path)
        return room_matches.verify(host,slug,remote,data['function'],data['source'],data['evidence'])
    link=SimpleNamespace(net=guest,slug=gslug,url='http://host',room='room',error="can't reach the host (ReadTimeout)",_req=request)
    guest.links[gslug]=link
    fn=guest._function('func_80000808',gslug);proj=guest.project_row(gslug)
    search=deep_permuter.Search(guest,1,10,Log(),fn)
    assert search._hold(agent,fn,count_turn=False)
    assert search._score_work(agent,proj,fn,DAMAGE,999999,None)==0
    assert guest._function(fn['name'],gslug)['status']=='matched'
    assert host._function(fn['name'],slug)['status']=='open'
    queued=room_matches.pending(guest,gslug,'client')
    assert len(queued)==1 and queued[0]['evidence']['compiler']
    assert not requests
    search._let_go(agent,fn)
    link.error=''
    assert room_matches.resend(link,queued[0]['key'])['matched']
    assert host._function(fn['name'],slug)['status']=='matched'
    assert not room_matches.pending(guest,gslug,'client')
    assert not deep_room.offline(link)


def test_offline_best_scores_require_identical_host_inputs(pair):
    from decompnet.app import deep_room
    host,slug,guest,gslug=pair
    name='func_80000808'
    evidence=room_matches.packet(guest,gslug,name,DAMAGE)
    assert deep_room.comparable(host,slug,name,evidence)
    assert not deep_room.comparable(host,slug,name,{**evidence,'target_sha256':'changed'})
    assert not deep_room.comparable(host,slug,name,{**evidence,'header_sha256':'changed'})
    assert not deep_room.comparable(host,slug,name,{**evidence,'compiler':{'toolchain':evidence['compiler']['toolchain'],'flags':['-O0']}})


def test_exhausted_profiles_leave_queue_but_keep_evidence(pair,monkeypatch):
    host,slug,_,_=pair
    agent,_=host.local_agent('client')
    monkeypatch.setattr(host,'attempt',lambda *args:{'matched':False,'score':1})
    profile={'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}
    monkeypatch.setattr(room_matches,'project_profiles',lambda *args:[profile])
    def miss(*args):
        from decompnet.network import NetworkError
        raise NetworkError('Automatic bundle recheck failed linked-byte/data verification. Bytes differ.')
    monkeypatch.setattr(room_matches.prepared_evidence,'promote_measured',miss)
    result=room_matches.verify(host,slug,agent,'func_80000808',DAMAGE)
    assert result['rejected'] and not result['pending'] and not result['matched']
    assert not room_matches.pending(host,slug)
    saved=json.loads(host.store.one('SELECT value FROM settings WHERE key=?',result['submission'])['value'])
    assert saved['status']=='rejected' and saved['source']==DAMAGE
    assert host._function('func_80000808',slug)['status']=='open'


@pytest.mark.parametrize('message',['Prepared inputs changed; retry.','Compiler timed out; retry.','Host resource limit: disk quota exceeded.'])
def test_changed_inputs_are_not_retired_as_failed_profiles(pair,monkeypatch,message):
    host,slug,_,_=pair
    agent,_=host.local_agent('client')
    monkeypatch.setattr(host,'attempt',lambda *args:{'matched':False,'score':1})
    monkeypatch.setattr(room_matches,'project_profiles',lambda *args:[{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}])
    def changed(*args):
        from decompnet.network import NetworkError
        raise NetworkError(message)
    monkeypatch.setattr(room_matches.prepared_evidence,'promote_measured',changed)
    result=room_matches.verify(host,slug,agent,'func_80000808',DAMAGE)
    assert result['pending'] and not result.get('rejected')
    assert room_matches.pending(host,slug)
