"""Browser capabilities do not bypass private APIs, project scope, freshness or the normal judge."""
import json

import pytest

from decompnet.app.browser_bridge import Bridge, origin_value

from .test_app_server import app as app_fixture
from .test_similar_matches import pair as pair_fixture

pair = pair_fixture
app = app_fixture
ORIGIN = 'http://127.0.0.1:4179'


def prepared(net):
    root = net.home if hasattr(net, 'home') else None
    proj = net.project_row('destination')
    from pathlib import Path
    root = Path(proj['root']); root.mkdir(exist_ok=True)
    fn = net.store.one('SELECT * FROM functions WHERE project_id=?', proj['id'])
    rom = bytearray(8192); rom[4096:4096+len(fn['target'])] = fn['target']
    (root/'rom.z64').write_bytes(rom)
    cfg = json.loads(proj['config']); cfg['rom']['file'] = 'rom.z64'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), proj['id']))
        db.execute('UPDATE functions SET rom_offset=4096 WHERE id=?', (fn['id'],))
    return fn['id']


def paired(net):
    bridge = Bridge(net)
    code = bridge.create_code('destination', ORIGIN)
    token = bridge.pair(ORIGIN, code['code'])['token']
    key, session = bridge.auth(ORIGIN, token)
    return bridge, key, session, token


def test_pairing_is_origin_bound_single_use_revocable_and_expiring(pair):
    prepared(pair)
    bridge = Bridge(pair)
    code = bridge.create_code('destination', ORIGIN)
    assert bridge.allowed(ORIGIN) and not bridge.allowed('https://other.example')
    with pytest.raises(ValueError): bridge.pair('https://other.example', code['code'])
    token = bridge.pair(ORIGIN, code['code'])['token']
    with pytest.raises(ValueError): bridge.pair(ORIGIN, code['code'])
    with pytest.raises(PermissionError): bridge.auth('https://other.example', token)
    bridge.revoke('destination')
    with pytest.raises(PermissionError): bridge.auth(ORIGIN, token)


def test_task_binds_project_and_snapshot_and_does_not_claim(pair):
    fid = prepared(pair)
    bridge, key, session, _ = paired(pair)
    task = bridge.task(key, session, fid)
    assert task['romOffset'] == 4096 and task['compiler']['id'] == 'gcc-2.7.2-kmc'
    assert len(task['targetSha256']) == 64 and len(task['romSha256']) == 64
    assert not pair.store.one('SELECT 1 FROM claims')
    with pytest.raises(ValueError): bridge.task(key, session, 1)
    loaded = bridge.consume(key, session, task['id'])
    with pytest.raises(ValueError): bridge.consume(key, session, task['id'])
    with pair.store.tx() as db: db.execute("UPDATE context_versions SET header='typedef int changed;' WHERE project_id=2")
    with pytest.raises(ValueError, match='changed'): bridge.check(key, session, loaded)


def test_submit_uses_host_judge_and_rechecks_authorization_before_record(pair, monkeypatch):
    fid = prepared(pair)
    bridge,key,session,_ = paired(pair)
    packet = bridge.task(key, session, fid)
    task = bridge.consume(key, session, packet['id'])
    def judge(agent, proj, fn, source, before_record):
        bridge.revoke('destination')
        pair._record(fn, agent, 0, source, 1, matched=True, before_record=before_record)
    monkeypatch.setattr(pair, 'try_quietly', judge)
    with pytest.raises(PermissionError): bridge.submit(key, session, task, 'int new(void){return 0;}')
    assert pair.store.one('SELECT status FROM functions WHERE id=?', fid)['status'] == 'open'
    assert not pair.store.one('SELECT 1 FROM attempts')
    assert not session['busy']


def test_submit_keeps_only_host_judged_improvement(pair, monkeypatch):
    fid = prepared(pair)
    bridge,key,session,_ = paired(pair)
    packet = bridge.task(key, session, fid)
    task = bridge.consume(key, session, packet['id'])
    def judge(agent, proj, fn, source, before_record):
        pair._record(fn, agent, 20, source, 1, before_record=before_record)
        return {'score':20,'matched':False}
    monkeypatch.setattr(pair, 'try_quietly', judge)
    result = bridge.submit(key,session,task,'int new(void){return 0;}')
    assert result['improved'] and not result['matched'] and result['hostVerified']
    assert pair.store.one('SELECT best_score FROM functions WHERE id=?',fid)['best_score'] == 20


@pytest.mark.parametrize('origin', ['null','file:///tmp/x','http://example.com','https://x.example/path','https://u:p@x.example'])
def test_invalid_origins_rejected(origin):
    with pytest.raises(ValueError): origin_value(origin)


def test_browser_route_preflight_authorization_size_and_private_api_separation(app, monkeypatch):
    client,net,*_ = app
    bridge = net.browser_bridge
    monkeypatch.setattr(bridge, 'project', lambda s: {'slug':s})
    assert client.post('/api/browser-bridge/authorize',json={'slug':'test','origin':ORIGIN}).status_code == 403
    auth = client.post('/api/browser-bridge/authorize',headers={'X-Decomp':'1'},json={'slug':'test','origin':ORIGIN})
    assert auth.status_code == 200
    denied = client.options('/api/browser-bridge/pair',headers={'Origin':'https://other.example','Access-Control-Request-Method':'POST'})
    assert denied.status_code == 403 and 'Access-Control-Allow-Origin' not in denied.headers
    pre = client.options('/api/browser-bridge/pair',headers={'Origin':ORIGIN,'Access-Control-Request-Method':'POST'})
    assert pre.status_code == 204 and pre.headers['Access-Control-Allow-Origin'] == ORIGIN
    answer = client.post('/api/browser-bridge/pair',headers={'Origin':ORIGIN},json={'code':auth.json()['code']})
    assert answer.status_code == 200
    token = answer.json()['token']
    assert client.post('/api/browser-bridge/task',headers={'Origin':ORIGIN},json={}).status_code == 401
    assert client.post('/api/browser-bridge/task',headers={'Origin':ORIGIN,'Authorization':'Bearer '+token},content='x'*(321*1024)).status_code == 400
    # A capability is not the window secret or a full Network agent token.
    assert client.get('/api/state',headers={'User-Agent':'Mozilla/5.0','Origin':ORIGIN,'Authorization':'Bearer '+token}).status_code in (401,403)


@pytest.mark.parametrize('source', ['#include "/tmp/secret"\nint x;', '%:include "x"', '??=include "x"', 'int x(){__asm__(".incbin x");}', 'int x __attribute__((section("x")));'])
def test_bridge_rejects_file_loading_and_assembly_extensions(source):
    from decompnet.app.browser_bridge import validate_source
    with pytest.raises(ValueError): validate_source(source)


def test_capability_expiry_removes_pending_tasks(pair):
    fid = prepared(pair)
    bridge,key,session,token = paired(pair)
    packet = bridge.task(key,session,fid)
    session['expires'] = 0
    with pytest.raises(PermissionError): bridge.auth(ORIGIN,token)
    assert packet['id'] not in bridge.tasks


@pytest.mark.parametrize('use_variant',[False,True])
def test_real_submission_uses_original_compiler_and_linked_byte_judge(pair, monkeypatch, use_variant):
    from decompnet import toolchains
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('needs installed KMC compiler')
    fid = prepared(pair)
    from pathlib import Path
    proj = pair.project_row('destination')
    cfg = json.loads(proj['config']); cfg['segments'] = []
    target = bytes.fromhex('03e0000800851021')
    path = Path(proj['root'])/'rom.z64'; rom = bytearray(path.read_bytes());rom[4096:4104] = target;path.write_bytes(rom)
    with pair.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
        db.execute('UPDATE functions SET target=?,size=8,n_insns=2 WHERE id=?',(target,fid))
    monkeypatch.setattr(pair.twins, 'after_match', lambda *a: None)
    bridge,key,session,_ = paired(pair)
    packet = bridge.task(key,session,fid)
    task = bridge.consume(key,session,packet['id'])
    variant=next(v for v in packet['compilerVariants'] if '-O1' in v['flags']) if use_variant else None
    outcome = bridge.submit(key,session,task,'int new(int a,int b) { return a+b; }', variant['id'] if variant else None)
    assert outcome['matched'] and outcome['hostVerified']
    assert pair.store.one('SELECT status FROM functions WHERE id=?',fid)['status'] == 'matched'
    if use_variant:
        from decompnet.project import compiler_for
        updated=pair.project_row('destination')
        assert compiler_for(json.loads(updated['config']), packet['vram'])['flags']==variant['flags']


def test_task_offers_only_supported_host_generated_variations(pair, monkeypatch):
    fid=prepared(pair)
    monkeypatch.setattr(pair,'flag_variants',lambda *a:[['-O1'],['-O1'],['-O2','-funrecognised']])
    bridge,key,session,_=paired(pair)
    packet=bridge.task(key,session,fid)
    assert [v['flags'] for v in packet['compilerVariants']]==[['-O2'],['-O1']]
    task=bridge.consume(key,session,packet['id'])
    with pytest.raises(ValueError,match='offered'):
        bridge.submit(key,session,task,'int new(void){return 0;}','invented-variant')
    assert not pair.store.one('SELECT 1 FROM attempts')
    assert not session['busy']


def test_revoked_variant_cannot_publish_profile_or_match(pair, monkeypatch):
    fid=prepared(pair)
    bridge,key,session,_=paired(pair)
    packet=bridge.task(key,session,fid)
    variant=next(v for v in packet['compilerVariants'] if v['flags']!=packet['compiler']['flags'])
    task=bridge.consume(key,session,packet['id'])
    original=pair.project_row('destination')['config']
    def compile_and_revoke(*args):
        from types import SimpleNamespace
        bridge.revoke('destination')
        return SimpleNamespace(ok=True,obj=b'object',effective_settings={})
    monkeypatch.setattr(pair,'_compile',compile_and_revoke)
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *a:True)
    monkeypatch.setattr(pair,'_evaluate',lambda *a:(object(),True,[]))
    with pytest.raises(PermissionError):bridge.submit(key,session,task,'int new(void){return 0;}',variant['id'])
    assert pair.project_row('destination')['config']==original
    assert not pair.store.one('SELECT 1 FROM attempts')
    assert not pair.store.one('SELECT 1 FROM claims')


def test_claimed_function_can_be_listed_and_fetched_without_disturbing_agent(pair):
    from decompnet.network import NetworkError
    fid=prepared(pair)
    worker,_=pair.local_agent('active-worker')
    fn=pair.store.one('SELECT * FROM functions WHERE id=?',fid)
    assert pair.permuter._hold(worker,fn,count_turn=False)
    source='int new(void) { return 7; }'
    with pair.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=20 WHERE id=?',(source,fid))
    original=dict(pair.store.one('SELECT * FROM claims WHERE function_id=?',fid))
    bridge,key,session,_=paired(pair)
    listed=bridge.listing(session,'')
    assert any(row['id']==fid for row in listed['functions'])
    packet=bridge.task(key,session,fid)
    assert packet['source']==source
    assert dict(pair.store.one('SELECT * FROM claims WHERE function_id=?',fid))==original
    task=bridge.consume(key,session,packet['id'])
    with pytest.raises(NetworkError,match='claimed'):
        bridge.submit(key,session,task,source)
    assert dict(pair.store.one('SELECT * FROM claims WHERE function_id=?',fid))==original
    assert not pair.store.one('SELECT 1 FROM attempts')
    assert not session['busy']
