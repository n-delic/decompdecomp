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
        assert fn['status']=='open' and room_matches.pending(host,slug)[0]['source']==source


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
    assert result['pending'] and seen==profiles
