"""Homemade images test byte coverage and evidence without commercial game inputs."""
import hashlib
import json
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet.app import rom_explorer, rom_scan, source_runtime, tools
from decompnet.app.agents import Manager, Session
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs
from decompnet.network import NetworkError
from decompnet.rom.explorer import DECODE_BYTES, PAGE_BYTES, analyse
from tests.test_source_runtime import make_app, worker_checkouts


def mapping(name='main',start=0,end=128,vram=0x80000000):
    return dict(name=name,rom_start=start,rom_end=end,vram=vram)


def function(name='reader',offset=0,vram=0x80000000,relocs=()):
    return dict(name=name,rom_offset=offset,vram=vram,size=8,unit='main',relocs=list(relocs))


def test_every_byte_is_covered_once_even_with_overlaps_and_unmapped_gaps():
    image=bytes(PAGE_BYTES*2+13)
    report=analyse(image,[mapping(end=64),mapping('other',32,96)],
                   [function(),function('overlap',4,0x80000004)],
                   [dict(name='label',address=0x80000020,size=16,kind='data')])
    rows=report['regions']
    assert rows[0]['start']==0 and rows[-1]['end']==len(image)
    assert all(a['end']==b['start'] for a,b in zip(rows,rows[1:]))
    assert sum(r['size'] for r in rows)==len(image)==sum(report['totals'].values())
    assert any(len(r['functions'])==2 for r in rows)
    assert any(not r['mappings'] for r in rows)


def test_references_cover_symbol_interiors_and_preserve_overlay_ambiguity():
    segs=[mapping('a',0,64),mapping('b',64,128),mapping('shared',128,192,0x90000000)]
    funcs=[function('local',0,relocs=[(4,5,0x80000024)]),function('outside',128,0x90000000,[(4,5,0x80000024)])]
    report=analyse(bytes(192),segs,funcs,[dict(name='table',address=0x80000020,size=16,kind='data')])
    refs=[(r,ref) for r in report['regions'] for ref in r['references']]
    local=[(r,v) for r,v in refs if v['function']=='local']
    outside=[v for r,v in refs if v['function']=='outside']
    assert len(local)==1 and local[0][1]['offset']==0x24 and local[0][1]['instruction']==0x80000004
    assert local[0][1]['confidence']=='recorded'
    assert len(outside)==2 and all(v['confidence']=='tentative' for v in outside)
    assert all(s['ambiguous'] for r in report['regions'] for s in r['symbols'])


def test_compressed_stream_crosses_page_boundary_and_only_validated_extent_is_counted():
    stream=zlib.compress(b'hello world'*100)
    offset=PAGE_BYTES-1
    image=b'\0'*offset+stream+b'\0'*123
    result=analyse(image,[],[],[])
    rows=[r for r in result['regions'] if r['confidence']=='validated']
    assert rows and sum(r['size'] for r in rows)==len(stream)
    assert rows[0]['start']==offset
    assert rows[-1]['end']==offset+len(stream)
    assert all(r['evidence'][0]['expanded_size']==1100 for r in rows)
    assert all(r['kind']=='compressed' for r in rows)


def test_bombs_and_broken_headers_never_become_validated_assets():
    image=zlib.compress(bytes(DECODE_BYTES+1))+b'Yaz0'+bytes(30)+b'\x78\x9cgarbage'
    result=analyse(image,[],[],[])
    assert not any(r['confidence']=='validated' for r in result['regions'])
    assert any(e['label']=='Yaz0' for r in result['regions'] for e in r['evidence'])
    assert all(r['confidence']!='recorded' for r in result['regions'])


@pytest.mark.parametrize('big_endian',[True,False])
def test_pointer_table_chains_are_sampled_leads_in_both_byte_orders(big_endian):
    image=struct.pack('>8I' if big_endian else '<8I',*([0x80000030]*8))+bytes(96)
    result=analyse(image,[mapping()],[],[],big_endian=big_endian)
    row=result['regions'][0]
    assert row['pointers'][0]['destinations'][0]['rom']==48
    assert all(e['confidence']=='tentative' for e in row['evidence'])


def test_invalid_mapping_and_cooperative_stop():
    with pytest.raises(ValueError,match='segment'):analyse(b'a',[mapping()],[],[])
    def stopped():raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):analyse(bytes(PAGE_BYTES*2),[],[],[],check=stopped)


class Log:
    def __init__(self):self.updates=[]
    def __call__(self,line):pass
    def progress(self,value):self.updates.append(value)
    def check_cancel(self):pass


@pytest.mark.parametrize('value',[{}, {'vis':0,'inputs':'1:A'}, {'vis':36001,'inputs':'1:A'},
                                  {'vis':60,'inputs':''}, {'vis':60,'inputs':'@file'},
                                  {'vis':True,'inputs':'1:A'}, {'vis':60,'inputs':'1:UNKNOWN'},
                                  {'vis':60,'inputs':'1:SX=200'}, {'vis':60,'inputs':'2:A,1:'},
                                  {'vis':60,'inputs':'61:A'}])
def test_later_scene_rejects_invalid_limits_and_scripts(value):
    from decompnet.app import rom_explorer
    from decompnet.network import NetworkError

    with pytest.raises(NetworkError):rom_explorer.scene_request(value)
    assert rom_explorer.scene_request({'vis':36000,'inputs':'600:START,608:,700:SX=-80,708:'})['vis']==36000


def test_later_scene_waits_for_an_earlier_scan_without_losing_its_request(monkeypatch):
    import threading
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet.app import rom_explorer

    net=SimpleNamespace(progress=SimpleNamespace(view=nullcontext))
    entered,release = threading.Event(),threading.Event()
    seen=[]
    def run(_net,_slug,_log,scene):
        seen.append(scene)
        if scene is None:
            entered.set()
            assert release.wait(5)
        return scene
    monkeypatch.setattr(rom_explorer,'_run',run)
    first=threading.Thread(target=rom_explorer.run,args=(net,'game',Log()))
    later_log=Log()
    later=threading.Thread(target=rom_explorer.run,args=(net,'game',later_log,{'vis':60,'inputs':'1:A'}))
    first.start();assert entered.wait(5)
    later.start()
    for _ in range(100):
        if later_log.updates:break
        threading.Event().wait(.01)
    assert later_log.updates[-1]['phase']=='Waiting for the earlier ROM scan'
    release.set();first.join(5);later.join(5)
    assert not first.is_alive() and not later.is_alive()
    assert seen == [None,{'vis':60,'inputs':'1:A'}]


def test_stopped_waiting_scene_leaves_the_scan_queue(monkeypatch):
    import threading
    from contextlib import nullcontext
    from types import SimpleNamespace

    from decompnet.app import rom_explorer

    net=SimpleNamespace(progress=SimpleNamespace(view=nullcontext))
    entered,release = threading.Event(),threading.Event()
    def run(_net,_slug,_log,_scene):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(rom_explorer,'_run',run)
    first=threading.Thread(target=rom_explorer.run,args=(net,'game',Log()))
    first.start();assert entered.wait(5)
    class Stopped(Log):
        def check_cancel(self):raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):
        rom_explorer.run(net,'game',Stopped(),{'vis':60,'inputs':'1:A'})
    release.set();first.join(5)
    assert not first.is_alive() and (id(net),'game') not in rom_explorer._scan_waiters


def test_later_scene_retains_prior_runtime_assets_and_bank_members():
    from decompnet.app.rom_scan_worker import _retain_assets

    bank=dict(offset=8,size=8,kind='3d model',layout='runtime-packed-model',codec='yay0',
              meshes=[dict(name='Mesh 1',commands='aa',base=0,vertices=3,triangles=1)],vertices=3,triangles=1)
    previous=dict(bank,meshes=[dict(name='Another name',commands='aa',base=0,vertices=3,triangles=1),
                               dict(name='Mesh 2',commands='bb',base=0,vertices=4,triangles=2)])
    texture=dict(offset=32,size=4,kind='image',layout='runtime-observed-texture')
    palette=dict(texture,palette_offset=100)
    assets=[bank]
    _retain_assets(assets,[previous,texture,palette])
    assert assets[1] == texture
    assert assets[2] == palette
    assert [m['commands'] for m in bank['meshes']] == ['aa','bb']
    assert (bank['vertices'],bank['triangles']) == (7,3)


def test_later_scene_bypasses_cache_and_keeps_prior_runtime_previews(project,monkeypatch):
    from decompnet.app import rom_explorer, rom_scan_worker
    from decompnet.consoles.n64 import emulator

    net,_,root=project
    monkeypatch.setattr(emulator,'installed',lambda:root)
    requests=[]
    def scan(_source,request,_log):
        requests.append(request)
        image=(root/'game.rom').read_bytes()
        at=64 if request['runtime_scene'] else 32
        assets=[dict(offset=at,size=4,kind='image',layout='runtime-observed-texture',confidence='validated',
                     preview_settings=dict(source='rom',format='auto',offset=at))]
        if request.get('previous_image_hash')==hashlib.sha256(image).hexdigest():
            rom_scan_worker._retain_assets(assets,request['previous_assets'])
        report=analyse(image,request['source']['segments'],request['functions'],request['symbols'],assets=assets)
        report['loader_flow']={'chains':[]};report['runtime_observation']='complete'
        return report
    monkeypatch.setattr(rom_explorer.rom_scan,'run',scan)
    rom_explorer.run(net,'game',Log())
    assert rom_explorer.run(net,'game',Log())['cached']
    result=rom_explorer.run(net,'game',Log(),{'vis':60,'inputs':'1:A,2:'})
    assert not result.get('cached') and len(requests)==2
    assert [a['offset'] for a in requests[1]['previous_assets']]==[32]
    with net.progress.view():report=rom_explorer.listing(net,'game')
    assert {r['start'] for r in report['regions'] if r['kind']=='image'} == {32,64}


@pytest.fixture
def project(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    image=bytes(8)+b'hello world\0'+bytes(108)
    (tmp_path/'game.rom').write_bytes(image)
    cfg=dict(kind='rom',platform='n64',rom=dict(file='game.rom',sha1=hashlib.sha1(image).hexdigest()),segments=[mapping()],compiler={'toolchain':'gcc-2.7.2-kmc','flags':[]})
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',('game','game','mips',1,str(tmp_path),json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,vram,rom_offset,unit) VALUES(?,?,?,?,?,?,?,?,?)',
                   (pid,'reader',8,2,bytes(8),json.dumps([[4,5,0x80000008]]),0x80000000,0,'main'))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'message',0x80000008,12,'string'))
    yield net,pid,tmp_path
    if hasattr(net,'autosave'):net.autosave.close()


@pytest.mark.parametrize('changed', ['version', 'source'])
def test_rescan_refreshes_assets_after_library_update(project, monkeypatch, changed):
    net, _, _ = project
    artifact = {'version': '1', 'source': '{"url":"https://example.test/assets-v1.tar.gz"}'}
    monkeypatch.setattr(rom_explorer, 'distribution', lambda _: SimpleNamespace(
        version=artifact['version'], read_text=lambda name: artifact['source'] if name == 'direct_url.json' else None,
    ))
    assert not rom_explorer.run(net, 'game', Log()).get('cached')
    assert rom_explorer.run(net, 'game', Log())['cached']
    artifact[changed] = '2' if changed == 'version' else '{"url":"https://example.test/assets-v2.tar.gz"}'
    assert not rom_explorer.run(net, 'game', Log()).get('cached')
    assert rom_explorer.run(net, 'game', Log())['cached']


def test_saved_map_preview_annotations_staleness_and_no_progress_side_effects(project,monkeypatch):
    net,pid,root=project
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Explorer must not synchronize or judge saved work'))
    before=[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    rom_explorer.run(net,'game',Log())
    with net.progress.view():
        report=rom_explorer.listing(net,'game')
        assert report['available'] and not report['stale']
        row=next(r for r in report['regions'] if r['kind']=='strings')
        assert row['used_by']==1 and row['function_refs']==['reader']
        packet=dict(region=row['id'],image_hash=report['image_hash'],name='Greeting',notes='Read by the reader.',kind='3d model')
        rom_explorer.annotate(net,'game',packet)
        assert 'hello world' in rom_explorer.detail(net,'game',row['id'],report['image_hash'])['preview']
        rom_explorer.run(net,'game',Log())
        assert next(r for r in rom_explorer.listing(net,'game')['regions'] if r['id']==row['id'])['annotation']['name']=='Greeting'
        assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['kind']=='3d model'
        # Bookkeeping and compiler changes do not invalidate the byte map.
        proj=net.project_row('game');cfg=dict(net.cfg(proj));cfg['progress_revision']='new';cfg['compiler']={'toolchain':'other'}
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        assert not rom_explorer.listing(net,'game')['stale']
        (root/'game.rom').write_bytes(bytes(128))
        assert rom_explorer.listing(net,'game')['stale']
        with pytest.raises(NetworkError,match='changed'):rom_explorer.annotate(net,'game',packet)
    assert [tuple(r) for r in net.store.all('SELECT * FROM functions')]==before


def test_cancelled_rescan_retains_complete_map_and_notes(project):
    net,pid,root=project
    rom_explorer.run(net,'game',Log())
    with net.progress.view():before=rom_explorer.listing(net,'game')
    log=Log()
    def stop():raise RuntimeError('stopped')
    log.check_cancel=stop
    with pytest.raises(RuntimeError,match='stopped'):rom_explorer.run(net,'game',log)
    with net.progress.view():assert rom_explorer.listing(net,'game')==before



def scan_source_with_pause(tmp_path, worker_source):
    """Hold the real child after analysis, before sending its report to the parent."""
    waiting, release = tmp_path / "scan-waiting", tmp_path / "scan-release"
    marker = '        with output.open("w", encoding="utf-8") as stream:'
    assert marker in worker_source
    slow = worker_source.replace(marker, f"""        emit("progress", dict(phase="Waiting to return the map", done=1, total=2))
        Path({str(waiting)!r}).touch()
        deadline = time.monotonic() + 30
        while not Path({str(release)!r}).exists() and time.monotonic() < deadline:
            time.sleep(.02)
{marker}""")
    return slow, waiting, release


def wait_for_scan(jobs, jid, waiting):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and jobs.jobs[jid]["state"] == "running":
        if waiting.exists() and jobs.jobs[jid].get("progress", {}).get("phase") == "Waiting to return the map":
            return
        time.sleep(.02)
    pytest.fail(f"The scan did not reach its reporting checkpoint: {jobs.jobs[jid]}")


def finished_scan(jobs, jid):
    deadline = time.monotonic() + 10
    while jobs.jobs[jid]["state"] == "running" and time.monotonic() < deadline:
        time.sleep(.02)
    return jobs.jobs[jid]


@pytest.mark.parametrize("update_kind", ["source-update", "local-edit"])
def test_worker_update_finishes_active_scan_and_keeps_agent_session_and_claim(project, monkeypatch, update_kind):
    net, _, root = project
    worker = "src/decompnet/app/rom_scan_worker.py"
    original = rom_scan.WORKER_SOURCE.read_text()
    old_source, waiting, release = scan_source_with_pause(root, original)
    new_source = original.replace('        resource_catalog.attach(report)',
                                  '        report["updated_scan_worker"] = True\n        resource_catalog.attach(report)')
    assert new_source != original
    source, staged, revision = worker_checkouts(root, worker, old_source, new_source)
    monkeypatch.setattr(rom_scan, "WORKER_SOURCE", source / worker)
    monkeypatch.setattr(source_runtime.edition, "CHECKOUT_ROOT", source)
    monkeypatch.setattr(source_runtime, "validate", lambda *args: (source, "git"))
    app = make_app(root)
    app.net = net
    app.jobs = jobs = Jobs()
    app.manager = manager = Manager(net, app.home, "http://localhost/mcp")
    agent, token = net.local_agent("scan-reload-helper")
    session = Session("helper", "api", "Scan helper", "game", agent.name, {}, state="working")
    manager.sessions[session.id] = session
    manager.assignments[agent.id] = session.slug
    session_id = manager._session_begin(session)
    tools.build_mcp(net, lambda ctx: (agent, session.slug), observe=manager.record_agent)
    runtime = source_runtime.Runtime(app)
    monkeypatch.setattr(runtime, "prepare", lambda *args: staged)
    monkeypatch.setattr(runtime, "drain", lambda: pytest.fail("a scan-worker update must keep the app running"))

    def claims():
        return json.loads(tools.call(net, net.authenticate(token), session.slug, "get_claims", {}))["claims"]

    with ThreadPoolExecutor(max_workers=1) as agent_loop:
        session.thread = agent_loop.submit(threading.current_thread).result(timeout=5)
        agent_loop.submit(net.claim_row, agent, "game", "reader").result(timeout=5)
        held = agent_loop.submit(claims).result(timeout=5)
        assert held[0]["function"] == "reader" and held[0]["claim_token"]
        jid = jobs.start("rom-explorer", lambda log: rom_explorer.run(net, "game", log), project="game")
        try:
            wait_for_scan(jobs, jid, waiting)
            if update_kind == "source-update":
                runtime.run(source, "git", "uv", root, "example/repo")
                assert runtime.job["state"] == "done", runtime.job
                assert app.build_id == revision
            else:
                edited = (source / worker).with_suffix(".tmp")
                edited.write_text(new_source)
                edited.replace(source / worker)
            assert rom_explorer.status(net, "game", jobs)["job"]["state"] == "running"
            assert not rom_explorer.listing(net, "game")["available"]
            assert agent_loop.submit(claims).result(timeout=5) == held
        finally:
            release.touch()

        job = finished_scan(jobs, jid)
        assert job["state"] == "done", job
        report = rom_explorer.listing(net, "game")
        assert report["size"] == 128 and not report["stale"] and "updated_scan_worker" not in report
        assert not rom_explorer.run(net, "game", Log()).get("cached")
        assert rom_explorer.listing(net, "game")["updated_scan_worker"]

        assert agent_loop.submit(claims).result(timeout=5) == held
        assert agent_loop.submit(threading.current_thread).result(timeout=5) is session.thread
        assert manager.sessions[session.id] is session and session.state == "working" and not session.stop.is_set()
        sessions = net.project_store("game").all("SELECT id, ended_at FROM helper_sessions WHERE helper_id=?", session.id)
        assert [dict(row) for row in sessions] == [{"id": session_id, "ended_at": None}]


def test_stopping_scan_worker_leaves_previous_map_and_annotations(project, monkeypatch):
    net, _, root = project
    rom_explorer.run(net, "game", Log())
    report = rom_explorer.listing(net, "game")
    rom_explorer.annotate(net, "game", dict(region=report["regions"][0]["id"], image_hash=report["image_hash"],
                                           name="A saved label"))
    previous = rom_explorer.listing(net, "game")
    worker = root / "slow_worker.py"
    slow_source, waiting, release = scan_source_with_pause(root, rom_scan.WORKER_SOURCE.read_text())
    worker.write_text(slow_source)
    monkeypatch.setattr(rom_scan, "WORKER_SOURCE", worker)
    jobs = Jobs()
    jid = jobs.start("rom-explorer", lambda log: rom_explorer.run(net, "game", log),
                     project="game", cancel_message="ROM scan stopped.")
    try:
        wait_for_scan(jobs, jid, waiting)
        assert jobs.cancel(jid)
        job = finished_scan(jobs, jid)
        assert job["state"] == "cancelled" and job["error"] == "ROM scan stopped.", job
        assert rom_explorer.listing(net, "game") == previous
    finally:
        release.touch()


def test_scan_rejects_a_report_from_before_a_layout_change(project, monkeypatch):
    net, pid, root = project
    rom_explorer.run(net, "game", Log())
    previous = rom_explorer._read(net, net.project_row("game"))
    worker = root / "slow_worker.py"
    source, waiting, release = scan_source_with_pause(root, rom_scan.WORKER_SOURCE.read_text())
    worker.write_text(source)
    monkeypatch.setattr(rom_scan, "WORKER_SOURCE", worker)
    jobs = Jobs()
    jid = jobs.start("rom-explorer", lambda log: rom_explorer.run(net, "game", log), project="game")
    try:
        wait_for_scan(jobs, jid, waiting)
        cfg = dict(net.cfg(net.project_row("game")), segments=[mapping(end=64)])
        with net.store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), pid))
    finally:
        release.touch()
    job = finished_scan(jobs, jid)
    assert job["state"] == "error" and "layout changed" in job["error"], job
    assert rom_explorer._read(net, net.project_row("game")) == previous
    assert rom_explorer.listing(net, "game")["stale"]


def test_status_is_scoped_and_never_loads_large_report(project,monkeypatch):
    net,pid,root=project;jobs=Jobs()
    jobs.jobs={'a':dict(id='a',kind='rom-explorer',activity_project='other',state='running'),
               'b':dict(id='b',kind='rom-explorer',activity_project='game',state='running')}
    monkeypatch.setattr(rom_explorer,'_read',lambda *a:pytest.fail('status loaded report'))
    with net.progress.view():assert rom_explorer.status(net,'game',jobs)['job']['id']=='b'


def test_routes_guard_start_deduplicate_and_reattach(project,monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app.server import build

    from .test_app_server import PORT, SECRET, WINDOW
    net,pid,root=project
    started,release=threading.Event(),threading.Event()
    calls=[]
    def scan(*args):calls.append(args);started.set();release.wait(5);return {'size':128}
    monkeypatch.setattr(rom_explorer,'run',scan)
    with net.progress.view():mcp,manager,security,_=build(net,root/'.decomp',PORT,SECRET)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Explorer route synchronized the project'))
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    try:
        assert client.get('/api/rom-explorer?slug=game',headers={'User-Agent':'outside'}).status_code==401
        assert client.post('/api/rom-explorer',json={'slug':'game'}).status_code==403
        first=client.post('/api/rom-explorer',json={'slug':'game'},headers={'X-Decomp':'1'})
        assert first.status_code==200,first.text
        assert started.wait(2)
        again=client.post('/api/rom-explorer',json={'slug':'game'},headers={'X-Decomp':'1'})
        assert again.json()['job']==first.json()['job']
        status=client.get('/api/rom-explorer?slug=game&view=status').json()
        assert status['job']['id']==first.json()['job'] and status['job']['state']=='running'
        scene=client.post('/api/rom-explorer',json={'slug':'game','scene':{'vis':60,'inputs':'1:A'}},headers={'X-Decomp':'1'})
        assert scene.status_code==200 and scene.json()['job']!=first.json()['job']
    finally:release.set()


def test_bss_is_runtime_only_and_never_adds_rom_bytes():
    report=analyse(bytes(128),[mapping()],
                   [function(relocs=[(4,5,0x90000004)])],
                   [dict(name='scratch',address=0x90000000,size=32,kind='bss')])
    assert sum(report['totals'].values())==128
    assert report['runtime_symbols'][0]['references']==[dict(function='reader',instruction=0x80000004,target=0x90000004)]
    assert not any(r['symbols'] for r in report['regions'])


def test_flattened_program_hash_is_not_mistaken_for_analysis_hash(project):
    net,pid,root=project
    with net.progress.view():
        proj=net.project_row('game');cfg=dict(net.cfg(proj))
        cfg['rom']=dict(cfg['rom'],program='program.elf',sha1='program-hash',analysis_sha1=hashlib.sha1((root/'game.rom').read_bytes()).hexdigest())
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        rom_explorer.run(net,'game',Log())
        report=rom_explorer.listing(net,'game')
        assert report['derived'] and 'not physical-media offsets' in report['note']
        assert report['image_hash']==hashlib.sha256((root/'game.rom').read_bytes()).hexdigest()


def test_hostile_header_density_is_bounded_but_byte_coverage_remains_complete(monkeypatch):
    from decompnet.rom import explorer
    monkeypatch.setattr(explorer,'MAX_FORMAT_CLUES',8)
    report=analyse(b'Yaz0'*100,[],[],[])
    assert report['format_limit'] and 'limit reached' in report['note']
    assert sum(r['size'] for r in report['regions'])==400
    assert len(report['regions'])<20


def test_rejected_and_unverified_compression_headers_do_not_classify_bytes(monkeypatch):
    import decompnet.rom.explorer as module
    bad=b'\x78\x9cgarbage'+b'\x1f\x8b\x08'+bytes(20)
    report=analyse(bad,[],[],[])
    assert not any(row['kind']=='compressed' for row in report['regions'])
    assert any(e['confidence']=='rejected' for row in report['regions'] for e in row['evidence'])
    monkeypatch.setattr(module,'MAX_DECODE_PROBES',0)
    report=analyse(zlib.compress(b'valid but unprobed'),[],[],[])
    assert not any(row['kind']=='compressed' for row in report['regions'])
    assert any('budget' in e['reason'] for row in report['regions'] for e in row['evidence'])


def test_native_resources_do_not_consume_the_generic_signature_budget(monkeypatch):
    import decompnet.rom.explorer as module
    monkeypatch.setattr(module,'MAX_FORMAT_CLUES',1)
    image=bytes(32)+zlib.compress(b'genuine compressed payload')
    asset=dict(offset=0,size=8,kind='data',label='Native record',confidence='validated')
    report=analyse(image,[],[],[],assets=[asset])
    assert any(r['kind']=='compressed' and r['confidence']=='validated' for r in report['regions'])


def _zip_image(files, *, before=1000, after=500):
    import io
    import zipfile
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for name,data,method in files:archive.writestr(name,data,compress_type=method)
    return b'\xff'*before+buffer.getvalue()+bytes(after)


def test_zip_members_are_named_by_their_path_and_validated_by_checksum():
    import zipfile
    stage=bytes(range(256))*300
    image=_zip_image([('Stages/',b'',zipfile.ZIP_STORED),('Stages/stage1.V3S',stage,zipfile.ZIP_DEFLATED),
                      ('scripts/start.ini',b'[race]\nlaps=3\n',zipfile.ZIP_STORED)])
    rows=analyse(image,[],[],[])['regions']
    named={r['archive_path']:r for r in rows if r['archive_path']}
    assert set(named)=={'Stages/','Stages/stage1.V3S','scripts/start.ini'}
    assert named['Stages/']['kind']=='container'
    # Every archive byte is named: the folder, both files and the central directory with its end record.
    archive=[r for r in rows if 1000<=r['start']<len(image)-500]
    assert archive and all(r['kind']!='unknown' for r in archive)
    assert any(e['label']=='ZIP central directory (3 entries)' for r in archive for e in r['evidence'])
    assert named['Stages/stage1.V3S']['kind']=='compressed' and named['Stages/stage1.V3S']['confidence']=='validated'
    assert named['scripts/start.ini']['kind']=='data' and named['scripts/start.ini']['confidence']=='validated'
    member=next(e for e in named['Stages/stage1.V3S']['evidence'] if e.get('archive_path'))
    assert member['label']=='ZIP member: Stages/stage1.V3S' and member['expanded_size']==len(stage)
    assert image[named['Stages/stage1.V3S']['start']:][:4]==b'PK\x03\x04'  # the span starts at the local header


def test_damaged_or_unexpanded_zip_members_are_not_validated(monkeypatch):
    import zipfile

    import decompnet.rom.explorer as module
    image=bytearray(_zip_image([('demo.dmo',b'recorded inputs '*8,zipfile.ZIP_STORED)]))
    image[image.index(b'recorded inputs')]^=0xff  # the payload no longer matches its checksum
    rows=analyse(bytes(image),[],[],[])['regions']
    assert not any(r['archive_path'] for r in rows)
    assert any(e.get('archive_path')=='demo.dmo' and e['confidence']=='rejected' for r in rows for e in r['evidence'])
    monkeypatch.setattr(module,'MAX_ARCHIVE_EXPANSION',0)
    rows=analyse(_zip_image([('demo.dmo',b'recorded inputs '*8,zipfile.ZIP_STORED)]),[],[],[])['regions']
    assert [r['confidence'] for r in rows if r['archive_path']]==['tentative']


@pytest.fixture
def fork(tmp_path):
    """A forked splat decomp with the user's game in it: no ROM path, segments or function offsets in its config."""
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    root=tmp_path/'decomp';root.mkdir()
    image=bytes(0x1000)+bytes(8)+b'hello world\0'+bytes(0x200)
    (root/'game.z64').write_bytes(image)
    (root/'other.z64').write_bytes(bytes(64))                      # never chosen: its hash isn't the game's
    (root/'game.yaml').write_text('options:\n  target_path: game.z64\n  base_path: .\nsegments:\n'
                                  '  - {name: main, type: code, start: 0x1000, vram: 0x80000400}\n  - [0x1100]\n')
    cfg=dict(kind='fork',platform='n64',fork_kind='splat',game_sha1=hashlib.sha1(image).hexdigest())
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',('game','game','',str(root),json.dumps(cfg))).lastrowid
    yield net,pid,root
    if hasattr(net,'autosave'):net.autosave.close()


def test_a_forked_decomp_explores_the_users_own_game(fork):
    net,pid,root=fork
    jobs=Jobs()
    with net.progress.view():
        assert rom_explorer.status(net,'game',jobs)['supported']
        rom_explorer.run(net,'game',Log())
        report=rom_explorer.listing(net,'game')
    assert report['available'] and not report['stale'] and report['image_name']=='game.z64' and not report['derived']
    text=next(r for r in report['regions'] if r['kind']=='strings')
    assert text['mappings']==[dict(segment='main',address=0x80000400+text['start']-0x1000)]
    # The fork's own work (functions without ROM offsets) never makes the map stale.
    with net.project_store(pid).tx() as db:
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs) VALUES(?,?,?,?,?,?)',(pid,'func_80000400',8,2,bytes(8),'[]'))
    with net.progress.view():
        assert not rom_explorer.listing(net,'game')['stale']
        assert 'hello wo' in rom_explorer.detail(net,'game',text['id'],report['image_hash'])['preview']


def test_a_fork_config_that_splits_another_image_maps_no_segments(fork):
    net,pid,root=fork
    (root/'game.yaml').write_text('options:\n  target_path: decompressed.z64\nsegments:\n'
                                  '  - {name: main, type: code, start: 0x1000, vram: 0x80000400}\n  - [0x1100]\n')
    with net.progress.view():
        rom_explorer.run(net,'game',Log())
        assert all(not r['mappings'] for r in rom_explorer.listing(net,'game')['regions'])


def test_a_fork_config_with_an_empty_code_segment_still_explores(fork):
    """An overlay whose next segment starts where it does maps nothing, and doesn't stop the scan."""
    net,pid,root=fork
    (root/'game.yaml').write_text('options:\n  target_path: game.z64\nsegments:\n'
                                  '  - {name: main, type: code, start: 0x1000, vram: 0x80000400}\n'
                                  '  - {name: empty, type: code, start: 0x1100, vram: 0x80100000}\n  - [0x1100]\n')
    with net.progress.view():
        rom_explorer.run(net,'game',Log())
        mapped={m['segment'] for r in rom_explorer.listing(net,'game')['regions'] for m in r['mappings']}
    assert mapped=={'main'}


def test_a_fork_without_the_recorded_game_is_explained(fork):
    net,pid,root=fork
    (root/'game.z64').write_bytes(bytes(0x1300))
    with net.progress.view():
        assert not rom_explorer.status(net,'game',Jobs())['supported']
        with pytest.raises(NetworkError,match='SHA-1 recorded'):rom_explorer.run(net,'game',Log())


def test_a_fork_shows_the_images_its_splat_config_declares(tmp_path):
    """Image segments of the config that splits the game become texture assets: raw, packed and with a palette."""
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    root=tmp_path/'decomp';root.mkdir()
    texels=bytes(range(16))
    packed=b'MIO0'+(16).to_bytes(4,'big')+(20).to_bytes(4,'big')+(20).to_bytes(4,'big')+b'\xff\xff\x00\x00'+texels
    image=bytearray(0x800)
    image[0x200:0x210]=bytes.fromhex('f801')*8                     # RGBA16 4×2, red
    image[0x300:0x300+len(packed)]=packed                          # I8 4×4 in a MIO0 block
    image[0x400:0x408]=bytes([0x01]*8)                             # CI4 4×4
    image[0x500:0x520]=bytes.fromhex('0000')+bytes.fromhex('07c1')*15
    (root/'game.z64').write_bytes(bytes(image))
    (root/'game.yaml').write_text('options:\n  target_path: game.z64\nsegments:\n'
                                  '  - name: assets\n    type: bin\n    start: 0x200\n    subsegments:\n'
                                  '      - [0x200, rgba16, red, 4, 2]\n      - [0x210]\n'
                                  '      - [0x300, mio0_image, grey, i8, 4, 4]\n      - [0x340]\n'
                                  '      - [0x400, ci4, green, 4, 4]\n      - [0x408]\n'
                                  '      - [0x500, palette, green]\n      - [0x520]\n'
                                  '      - [0x600, ci8, orphan, 4, 4]\n'
                                  '  - [0x800]\n')
    cfg=dict(kind='fork',platform='n64',fork_kind='splat',game_sha1=hashlib.sha1(bytes(image)).hexdigest())
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',('game','game','',str(root),json.dumps(cfg)))
    try:
        with net.progress.view():
            rom_explorer.run(net,'game',Log())
            report=rom_explorer.listing(net,'game')
            declared={r['detected_asset']['resource_path']:r for r in report['regions']
                      if (r.get('detected_asset') or {}).get('provider')=='splat-config'}
            assert set(declared)=={'red','grey','green'}                # a CI8 without a palette is left out
            assert declared['grey']['detected_asset']['packed']
            shown={name:rom_explorer.asset_preview(net,'game',dict(region=row['id'],image_hash=report['image_hash'],options={}))
                   for name,row in declared.items()}
        assert (shown['red']['details']['width'],shown['red']['details']['height'])==(4,2)
        assert shown['grey']['decoded']['codec']=='MIO0' and shown['grey']['details']['height']==4
        assert shown['green']['details']['palette']==0
    finally:
        if hasattr(net,'autosave'):net.autosave.close()


def test_a_fork_shows_the_images_its_json_asset_list_declares(tmp_path):
    """An assets.json in the style of the first 3D platformer's decompilation: formats in the paths, dims, palettes."""
    from decompnet.rom import config_images
    root=tmp_path/'decomp';root.mkdir()
    listing={'assets/textures/red.rgba16.png':{'meta':{'dims':[4,2]},'offsets':{'us':['0x200','0x0'],'jp':['0x300','0x0']}},
             'assets/textures/pal.rgba16.png':{'meta':{'dims':[4,4]},'offsets':{'us':['0x500','0x0']}},
             'assets/textures/green.ci4.png':{'meta':{'dims':[4,4],'pal':['assets/textures/pal.rgba16.png']},'offsets':{'us':['0x400','0x0']}},
             'assets/textures/lost.ci4.png':{'meta':{'dims':[4,4],'pal':['assets/textures/missing.rgba16.png']},'offsets':{'us':['0x400','0x0']}},
             'bin/code.bin':{'meta':{'size':'0x80'},'offsets':{'us':['0x0','0x0']}}}
    (root/'assets.json').write_text(json.dumps(listing))
    found={i['name']:i for i in config_images.asset_list_images(root,'us')}
    assert set(found)=={'textures/red.rgba16.png','textures/pal.rgba16.png','textures/green.ci4.png'}
    assert found['textures/green.ci4.png']['palette']==0x500 and found['textures/red.rgba16.png']['offset']==0x200
    assert [(i['name'],i['offset']) for i in config_images.asset_list_images(root,'jp')]==[('textures/red.rgba16.png',0x300)]
    assert config_images.asset_list_images(root,None)==[]                 # two releases listed, none chosen
    image=bytearray(0x800);image[0x200:0x210]=bytes.fromhex('f801')*8
    assets=config_images.regions(bytes(image),list(found.values()),'x')
    red=next(a for a in assets if a['resource_path']=='textures/red.rgba16.png')
    shown=config_images.preview(bytes(image),red,{})
    assert (shown['details']['width'],shown['details']['height'])==(4,2) and 'asset list' in red['reason']


def test_a_fork_shows_the_sno_texture_sheets_its_splat_config_declares():
    """A project's own sprite_sheet segment: sno-packed, found only by the start and size the config gives."""
    import struct

    from decompnet.rom import config_images
    palette=bytes.fromhex('f801')+bytes.fromhex('07c1')*15
    base=8+16+len(palette)
    sheet=struct.pack('>II',0,1)+struct.pack('>I6H',base,0,4,2,0,0,0)+palette+bytes([0x01,0x10]*2)
    packed=b''.join(bytes([0,value]) for value in sheet)
    image=bytearray(0x400);image[0x100:0x100+len(packed)]=packed
    config={'segments':[{'name':'menu','type':'sprite_sheet','start':0x100,'decompressed_size':len(sheet)},
                        {'name':'broken','type':'sprite_sheet','start':0x300,'decompressed_size':len(sheet)},[0x380]]}
    declared=config_images.sheets(config)
    assert [s['name'] for s in declared]==['menu','broken']
    assets=config_images.sheet_regions(bytes(image),declared,'x')
    assert [a['resource_path'] for a in assets]==['menu']                 # zeros don't unpack to a sheet
    shown=config_images.preview(bytes(image),assets[0],{})
    assert shown['decoded']['codec']=='sno' and (shown['details']['width'],shown['details']['height'])==(4,2)


def test_a_fork_with_one_config_per_release_reads_the_one_that_splits_its_game(tmp_path):
    """config/<release>/ layouts: another release's config comes first, the game's own is the one read."""
    from decompnet.forks.project import ForkProject
    from decompnet.rom import config_images
    root=tmp_path/'decomp';(root/'config'/'eur').mkdir(parents=True);(root/'config'/'aaa').mkdir()
    image=bytearray(0x400);image[0x210:0x220]=bytes.fromhex('f801')*8
    (root/'config'/'eur'/'baserom.eur.z64').write_bytes(bytes(image))
    (root/'config'/'aaa'/'game.aaa.yaml').write_text('options:\n  target_path: config/aaa/baserom.aaa.z64\n  base_path: ../../\nsegments:\n  - [0x0]\n')
    (root/'config'/'eur'/'game.eur.yaml').write_text(
        'options:\n  target_path: config/eur/baserom.eur.z64\n  base_path: ../../\nsegments:\n'
        '  - type: group\n    name: art\n    start: 0x200\n    subsegments:\n'
        '      - [auto, rgba16, first, 4, 2]\n      - { start: auto, type: rgba16, name: second, width: 4, height: 2 }\n'
        '  - [0x400]\n')
    config=ForkProject(root,create_state=False).splat_config_for(root/'config'/'eur'/'baserom.eur.z64',None)
    assert config and config['options']['target_path']=='config/eur/baserom.eur.z64'
    assert [(i['name'],i['offset']) for i in config_images.images(config)]==[('first',0x200),('second',0x210)]


def test_dialogue_preview_links_open_verified_targets_only():
    from decompnet.app.rom_explorer import _resource_links

    script = dict(offset=0, label='Dialogue', resource_type='dialogue', directory_offset=0,
                  cutscene_resources=[dict(offset=40, label='intro.ncs'), dict(offset=60, label='missing.ncs')])
    translation = dict(offset=10, label='Translation', resource_type='dialogue', directory_offset=0)
    stream = dict(offset=20, audio_kind='dean stream')
    other = dict(offset=30, audio_kind='other stream')
    cutscene = dict(offset=40, layout='scene-records', label='NCS cutscene commands')
    regions = [dict(id=str(a['offset']), start=a['offset'], end=a['offset'] + 10, detected_asset=a)
               for a in (script, translation, stream, other, cutscene)]
    result = dict(table=dict(rows=[dict(audio_offset=20), dict(audio_offset=30), {}]))
    _resource_links(result, script, dict(regions=regions))
    assert result['table']['rows'][0]['links'] == [
        dict(label='Open voice audio', region='20', offset=20, inspect=True)]
    assert all(not row['links'] for row in result['table']['rows'][1:])
    assert result['links'] == [dict(label='Translation', region='10', offset=10)]
    assert result['cutscene_links'] == [dict(label='intro.ncs', region='40', offset=40, inspect=True)]


def test_validated_preview_at_a_nested_resource_start_takes_precedence():
    texture = dict(offset=8,size=80,kind='image',media='image',confidence='validated',preview_settings={})
    model = dict(offset=24,size=12,kind='3d model',media='model',confidence='validated',preview_settings={})
    for assets in ([texture,model],[model,texture]):
        rows = analyse(bytes(128),[],[],[],assets=assets)['regions']
        assert next(row for row in rows if row['start']==8)['detected_asset']==texture
        assert next(row for row in rows if row['start']==24)['detected_asset']==model


def test_region_can_preview_two_detected_assets_at_the_same_offset(tmp_path, monkeypatch):
    from types import SimpleNamespace

    image = bytes(64)
    path = tmp_path/'image'
    path.write_bytes(image)
    digest = hashlib.sha256(image).hexdigest()
    settings = dict(source='rom', format='auto', offset=0)
    model = dict(kind='3d model', media='model', offset=0, preview_settings=settings)
    texture = dict(kind='image', media='image', offset=0, preview_settings=settings)
    row = dict(start=0, detected_asset=model, evidence=[model, texture])
    report = dict(size=len(image), regions=[row])
    console = SimpleNamespace(asset_scene=lambda *_: None,
                              asset_preview=lambda _, asset, selection: dict(media=asset['media']))
    net = SimpleNamespace(console=lambda _: console)
    monkeypatch.setattr(rom_explorer, '_current', lambda *_: ({}, dict(report=report), row))
    monkeypatch.setattr(rom_explorer, '_source', lambda *_: dict(path=path))
    monkeypatch.setattr(rom_explorer, '_resource_links', lambda *_: None)

    request = dict(region='0-40', image_hash=digest)
    assert rom_explorer.asset_preview(net, 'game', request)['media'] == 'model'
    assert rom_explorer.asset_preview(net, 'game', request | dict(asset_view='image'))['media'] == 'image'
    row['evidence'].remove(texture)
    with pytest.raises(NetworkError, match='no detected asset'):
        rom_explorer.asset_preview(net, 'game', request | dict(asset_view='image'))


def test_scoped_splat_data_symbols_only_cover_their_matching_overlay():
    segments = [mapping('bank_a', 0, 32), mapping('bank_b', 32, 64)]
    symbols = [dict(name='D_80000008_bank_b',address=0x80000008,size=4,kind='data'),
               dict(name='D_80000000_bank_b',address=0x80000010,size=4,kind='data')]
    report = analyse(bytes(64), segments, [], symbols)
    scoped = [(row['start'],symbol['ambiguous']) for row in report['regions']
              for symbol in row['symbols'] if symbol['name'] == symbols[0]['name']]
    ambiguous = [(row['start'],symbol['ambiguous']) for row in report['regions']
                 for symbol in row['symbols'] if symbol['name'] == symbols[1]['name']]
    assert scoped == [(40,False)]
    assert ambiguous == [(16,True),(48,True)]


def test_indexed_preview_reads_palette_from_a_separate_compressed_block(tmp_path, monkeypatch):
    """The palette offset is relative to the second decoded block, not the texels."""
    import base64

    from tests.test_rom_assets import pixels

    texels = zlib.compress(bytes([0, 1, 1, 0]))
    palette = zlib.compress(bytes.fromhex('f80107c1') + bytes(508))
    image = texels + palette
    path = tmp_path/'image.z64'
    path.write_bytes(image)
    report = {'size': len(image), 'image_hash': hashlib.sha256(image).hexdigest()}
    row = {'start': 0, 'detected_asset': None}
    monkeypatch.setattr(rom_explorer, '_current', lambda *args: ({}, {'report': report}, row))
    monkeypatch.setattr(rom_explorer, '_source', lambda *args: {'path': path})
    console = SimpleNamespace(asset_decode=lambda _: None)
    net = SimpleNamespace(console=lambda _: console)
    options = dict(source='decoded', block_offset=0, offset=0, format='ci8', width=2, height=2,
                   palette_block_offset=len(texels), palette_offset=0, palette_format='rgba5551-be')
    request = dict(region='0', image_hash=report['image_hash'], options=options)
    result = rom_explorer.asset_preview(net, 'game', request)
    rgba = pixels(base64.b64decode(result['data_url'].split(',')[1]))
    assert rgba[:8] == bytes([255, 0, 0, 255, 0, 255, 0, 255])
    assert result['decoded']['palette']['block_offset'] == len(texels)
    assert result['settings']['palette_offset'] == 0
    assert result['settings']['palette_block_offset'] == len(texels)
    with pytest.raises(NetworkError, match='Palette offset'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'palette_offset': 512}})
    with pytest.raises(NetworkError, match='decompressed indexed image'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'source': 'rom'}})


def test_rescan_after_runtime_emulator_becomes_available(project, monkeypatch):
    from decompnet.consoles.n64 import emulator

    net, _, root = project
    available = [False]
    monkeypatch.setattr(emulator, 'installed', lambda: root if available[0] else None)
    assert not rom_explorer.run(net, 'game', Log()).get('cached')
    assert rom_explorer.run(net, 'game', Log())['cached']
    available[0] = True
    assert not rom_explorer.run(net, 'game', Log()).get('cached')
    assert rom_explorer.run(net, 'game', Log())['cached']


def test_decoded_indexed_image_can_use_a_rom_palette_and_flip_rows(tmp_path, monkeypatch):
    import base64

    from tests.test_rom_assets import pixels

    texels = zlib.compress(bytes([0, 1, 1, 0]))
    palette = bytes.fromhex('f80107c1') + bytes(508)
    image = texels + palette
    path = tmp_path/'image.z64'
    path.write_bytes(image)
    report = {'size': len(image), 'image_hash': hashlib.sha256(image).hexdigest()}
    row = {'start': 0, 'detected_asset': None}
    monkeypatch.setattr(rom_explorer, '_current', lambda *args: ({}, {'report': report}, row))
    monkeypatch.setattr(rom_explorer, '_source', lambda *args: {'path': path})
    net = SimpleNamespace(console=lambda _: SimpleNamespace(asset_decode=lambda _: None))
    options = dict(source='decoded', block_offset=0, offset=0, format='ci8', width=2, height=2,
                   palette_rom_offset=len(texels), palette_offset=0, palette_format='rgba5551-be', flip_y=True)
    request = dict(region='0', image_hash=report['image_hash'], options=options)
    result = rom_explorer.asset_preview(net, 'game', request)
    rgba = pixels(base64.b64decode(result['data_url'].split(',')[1]))
    assert rgba[:8] == bytes([0, 255, 0, 255, 255, 0, 0, 255])
    assert result['decoded']['palette']['codec'] == 'ROM bytes'
    with pytest.raises(NetworkError, match='either'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'palette_block_offset': 0}})
    with pytest.raises(NetworkError, match='truncated'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'palette_rom_offset': len(image)-1}})
    with pytest.raises(NetworkError, match='vertical orientation'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'flip_y': 'true'}})
    with pytest.raises(NetworkError, match='decompressed indexed image'):
        rom_explorer.asset_preview(net, 'game', request | {'options': options | {'source': 'rom'}})


def test_detected_image_preview_survives_region_boundaries(tmp_path, monkeypatch):
    from decompnet.rom.asset_formats import _png

    encoded = _png(2, 2, bytes([255, 0, 0, 255]*4))
    offset = PAGE_BYTES - 8
    image = bytes(offset) + encoded + bytes(16)
    path = tmp_path/'image'
    path.write_bytes(image)
    report = analyse(image, [], [], [])
    row = next(row for row in report['regions'] if row['start'] == PAGE_BYTES)
    assert row['detected_asset']['offset'] == offset
    def console_preview(*_):
        raise AssertionError('Generic images must use the shared decoder')
    console = SimpleNamespace(asset_scene=lambda *_: None, asset_preview=console_preview)
    net = SimpleNamespace(console=lambda _: console)
    monkeypatch.setattr(rom_explorer, '_current', lambda *_: ({}, dict(report=report), row))
    monkeypatch.setattr(rom_explorer, '_source', lambda *_: dict(path=path))
    monkeypatch.setattr(rom_explorer, '_resource_links', lambda *_: None)
    shown = rom_explorer.asset_preview(net, 'game', dict(region=row['id'], image_hash=hashlib.sha256(image).hexdigest()))
    assert shown['media'] == 'image'
    assert shown['details']['width'] == shown['details']['height'] == 2
