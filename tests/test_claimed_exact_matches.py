"""Exact independent verdicts supersede claims; rejected verdicts leave workers alone."""
import json
import time
from types import SimpleNamespace as NS

import pytest

from decompnet import scoring
from decompnet.app.engine import autopilot
from decompnet.app.engine.autopilot import AutopilotMixin
from decompnet.network import Network, NetworkError


@pytest.fixture(params=['n64','ps1','ps2','psp'])
def judged(tmp_path,monkeypatch,request):
    net=Network(tmp_path/'app.db')
    winner=net.authenticate(net.register_agent('solver'))
    owner=net.authenticate(net.register_agent('owner'))
    cfg={'platform':request.param,'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,config,big_endian,root) VALUES(?,?,?,?,?)',
                       ('demo','Demo',json.dumps(cfg),1,str(tmp_path))).lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram,target,meta) "
                       "VALUES(?,'target',16,4,'open','',4096,?,'{}')",(pid,b'original')).lastrowid
        db.execute("INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,'',1,'typedef int word;')",(pid,))
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,owner.id,time.time(),time.time()+300))
    monkeypatch.setattr(net,'_compile',lambda *args:NS(ok=True,obj=b'object'))
    monkeypatch.setattr(net,'_evaluate',lambda *args:(NS(code=b'code',relocs=[]),True,[]))
    monkeypatch.setattr(net,'_target_obj',lambda *args:object())
    monkeypatch.setattr(autopilot.scoring,'diff',lambda *args:scoring.DiffResult(0,4,[],True))
    return net,winner,owner,pid,fid


def fn(net,fid):
    return net.store.one('SELECT * FROM functions WHERE id=?',fid)


def attempt(judged):
    net,winner,_,pid,fid=judged
    return AutopilotMixin.try_quietly(net,winner,net.store.one('SELECT * FROM projects WHERE id=?',pid),
                                     fn(net,fid),'int target(void){return 0;}',exact_only=True)


def test_exact_match_releases_other_worker_only_after_commit(judged):
    net,winner,owner,pid,fid=judged
    calls=[];net.on_external_match=lambda *args:calls.append(args)
    with net.store.tx():
        assert attempt(judged)['matched']
        assert not calls
    assert calls==[(owner.id,'target',pid)]
    assert fn(net,fid)['status']=='matched' and fn(net,fid)['matched_agent_id']==winner.id
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fid)


def test_match_rollback_does_not_interrupt_owner(judged):
    net,_,owner,_,fid=judged
    calls=[];net.on_external_match=lambda *args:calls.append(args)
    with pytest.raises(RuntimeError):
        with net.store.tx():
            attempt(judged)
            raise RuntimeError('rollback')
    assert not calls
    assert fn(net,fid)['status']=='open'
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==owner.id


@pytest.mark.parametrize('change',['target','meta','symbols','abi','build','removed','config','command'])
def test_changed_build_inputs_do_not_displace_owner(judged,monkeypatch,change):
    net,_,owner,pid,fid=judged
    def compile(*args):
        with net.store.tx() as db:
            if change=='target':db.execute('UPDATE functions SET target=? WHERE id=?',(b'changed',fid))
            if change=='meta':db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps({'real':'other'}),fid))
            if change=='symbols':db.execute("INSERT INTO symbols(project_id,name,address) VALUES(?,'changed',8192)",(pid,))
            if change=='abi':db.execute('UPDATE projects SET big_endian=0 WHERE id=?',(pid,))
            if change=='removed':db.execute('DELETE FROM functions WHERE id=?',(fid,))
            if change in ('config','command'):
                cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=?',(pid,)).fetchone()['config'])
                if change=='config':cfg['unknown_build_setting']='changed'
                else:cfg['compiler']['cmd']=['another compiler']
                db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
            if change=='build':
                from pathlib import Path
                root=net.store.one('SELECT root FROM projects WHERE id=?',pid)['root']
                Path(root,'Makefile').write_text('changed build')
        return NS(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile)
    with pytest.raises(NetworkError,match='inputs changed'):attempt(judged)
    if change != 'removed':
        assert fn(net,fid)['status']=='open'
        assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==owner.id
    assert not net.store.all('SELECT * FROM attempts')


def test_newer_agent_draft_does_not_invalidate_an_exact_frozen_source(judged,monkeypatch):
    net,_,_,_,fid=judged
    def compile(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='new draft' WHERE id=?",(fid,))
        return NS(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile)
    assert attempt(judged)['matched']
    assert fn(net,fid)['matched_source']=='int target(void){return 0;}'


def test_nonmatch_preserves_claim_and_attempts(judged,monkeypatch):
    net,_,owner,_,fid=judged
    monkeypatch.setattr(net,'_evaluate',lambda *args:(NS(code=b'code',relocs=[]),False,[]))
    assert not attempt(judged)['matched']
    assert fn(net,fid)['status']=='open'
    assert not net.store.all('SELECT * FROM attempts')
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==owner.id


def test_ordinary_quiet_attempt_still_respects_claim(judged):
    net,winner,_,pid,fid=judged
    with pytest.raises(NetworkError,match='claimed'):
        AutopilotMixin.try_quietly(net,winner,net.store.one('SELECT * FROM projects WHERE id=?',pid),fn(net,fid),'C')


def test_exact_save_leaves_applying_agents_other_claim_alone(judged):
    net,winner,_,pid,_=judged
    with net.store.tx() as db:
        other=db.execute("INSERT INTO functions(project_id,name,status) VALUES(?,'other','open')",(pid,)).lastrowid
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(other,winner.id,time.time(),time.time()+300))
    assert attempt(judged)['matched']
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',other)['agent_id']==winner.id


def test_worker_notification_failure_cannot_undo_or_fail_saved_match(judged,caplog):
    net,_,_,_,fid=judged
    def broken(*args):raise RuntimeError('worker unavailable')
    net.on_external_match=broken
    assert attempt(judged)['matched']
    assert fn(net,fid)['status']=='matched'
    assert 'Could not redirect worker' in caplog.text


@pytest.mark.parametrize('change', ['workflow', 'header_version', 'header', 'profile', 'both'])
def test_exact_context_is_saved_despite_concurrent_work(judged, monkeypatch, change):
    from decompnet import match_bundle
    from decompnet.project import compiler_for
    net, _, owner, pid, fid = judged
    original_header = net._context(pid, '')['header']
    def compile(*args):
        assert args[1] == original_header
        with net.store.tx() as db:
            cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
            cfg.update(spend={'tokens':123}, auto_commit_matches=True, autopilot={'tried':99}, progress_revision='new')
            if change in ('profile','both'):cfg['compiler']['flags'] = ['-O1']
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
            if change == 'header_version':
                db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid,'',2,original_header))
            if change in ('header','both'):
                db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid,'',2,'typedef long word;'))
        return NS(ok=True, obj=b'object', effective_settings={'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}})
    monkeypatch.setattr(net, '_compile', compile)
    assert attempt(judged)['matched']
    saved = fn(net, fid)
    assert saved['status'] == 'matched'
    assert net._context(pid, saved['unit'])['header'] == original_header
    cfg = json.loads(net.store.one('SELECT config FROM projects WHERE id=?', pid)['config'])
    assert cfg['spend']['tokens'] == 123 and cfg['autopilot']['tried'] == 99
    assert compiler_for(cfg, saved['vram'], saved)['flags'] == ['-O2']
    if change in ('profile','both'):assert cfg['compiler']['flags'] == ['-O1']
    if change in ('header','both'):
        assert saved['unit'] != ''
        assert net._context(pid, '')['header'] == 'typedef long word;'
    assert not match_bundle.status(net, saved)['changed_inputs']
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?', fid)
    bundle = net.store.one('SELECT * FROM verified_matches WHERE function_id=?', fid)
    assert bundle['header'] == original_header
    assert json.loads(bundle['details'])['compiler']['flags'] == ['-O2']



def test_cancel_before_publication_keeps_shared_changes_but_no_partial_bundle(judged, monkeypatch):
    net, winner, owner, pid, fid = judged
    def compile(*args):
        with net.store.tx() as db:
            db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',
                       (pid,'',2,'new shared header'))
            cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=?',(pid,)).fetchone()['config'])
            cfg['compiler']['flags']=['-O1']
            db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        return NS(ok=True,obj=b'object')
    monkeypatch.setattr(net,'_compile',compile)
    def cancelled():raise NetworkError('Cancelled before saving')
    with pytest.raises(NetworkError,match='Cancelled'):
        AutopilotMixin.try_quietly(net,winner,net.store.one('SELECT * FROM projects WHERE id=?',pid),
            fn(net,fid),'int target(void){return 0;}',before_record=cancelled,exact_only=True)
    assert fn(net,fid)['status']=='open' and fn(net,fid)['unit']==''
    assert not net.store.all('SELECT * FROM attempts')
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==owner.id
    assert not json.loads(net.store.one('SELECT config FROM projects WHERE id=?',pid)['config']).get('function_compilers')
    assert net._context(pid,'')['header']=='new shared header'
