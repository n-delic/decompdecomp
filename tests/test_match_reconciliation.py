import json
import threading
from types import SimpleNamespace

import pytest

from decompnet import match_bundle
from decompnet.app import match_reconciliation as recovery
from tests.test_prepared_evidence import prepared_net


def setup(monkeypatch, *, best=20, exact=True, worker=False):
    from decompnet.app.permute import Permuter
    from decompnet.app.permuter_worker import PermuterWorker
    net,proj,fn=prepared_net()
    net.is_rom=lambda p:True;net.fork_slug=lambda s:None;net.links={}
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE id=1')
    net.local_agent=lambda name:(SimpleNamespace(id=1,name='autopilot'), '')
    net.permuter=PermuterWorker(net) if worker else Permuter(net)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('UPDATE functions SET best_score=?,matched_source=NULL', (best,))
    monkeypatch.setattr(recovery,'bank',lambda *a:[])
    monkeypatch.setattr(recovery,'candidates',lambda *a:[('int f(void){return 1;}','private types','historical')])
    monkeypatch.setattr(recovery.candidate_scope,'key',lambda *a:'scope')
    settings=json.loads(proj['config'])['compiler']
    net._compile=lambda *a:SimpleNamespace(ok=True,obj=b'fixture',effective_settings={'compiler':settings})
    net._evaluate=lambda *a:(object(),exact,[])
    log=lambda text:None
    log.cancel_event=threading.Event();log.progress=lambda p:None
    return net,log


@pytest.mark.parametrize('best',[20,0])
@pytest.mark.parametrize('worker',[False,True])
def test_only_fresh_exact_adopts_private_header_even_if_old_open_score_is_zero(monkeypatch,best,worker):
    net,log=setup(monkeypatch,best=best,worker=worker)
    out=recovery.run(net,'game',30,1,log)
    assert out['matched']==1 and out['attempts']==1
    fn=net.store.one('SELECT * FROM functions')
    assert fn['status']=='matched' and fn['unit'].startswith('local_source_')
    assert net._context(1,fn['unit'])['header']=='private types'
    assert not net.store.one('SELECT 1 FROM claims')


def test_nonexact_cancelled_and_changed_inputs_never_promote(monkeypatch):
    net,log=setup(monkeypatch,exact=False)
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'
    net,log=setup(monkeypatch)
    def compile(*a):
        log.cancel_event.set()
        return SimpleNamespace(ok=True,obj=b'fixture')
    net._compile=compile
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'


def test_source_aliasing_preserves_strings_and_requires_target_identity(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True
    with net.store.tx() as db:db.execute("UPDATE functions SET target=X'01020304'")
    fn=net.store.one('SELECT * FROM functions')
    source='int old(void){say("old");return 1;}'
    proof=dict(abi=match_bundle.abi(proj,json.loads(proj['config'])),vram=fn['vram'],target_sha256=match_bundle.digest(fn['target']),
               relocations_sha256=match_bundle.digest(fn['relocs'] or ''),header_sha256=match_bundle.digest('private'))
    row=dict(name='old',source=source,header='private',meta='{}',vram=fn['vram'])
    with net.store.tx() as db:db.execute('DELETE FROM attempts')
    out=recovery.candidates(net,proj,fn,[(row,proof)])
    assert any('int f(void)' in s and 'say("old")' in s for s,h,o in out)
    assert not recovery.candidates(net,proj,fn,[(row,{**proof,'target_sha256':'changed'})])


def test_failed_compiler_candidates_rotate_across_runs(monkeypatch):
    net,log=setup(monkeypatch)
    monkeypatch.setattr(recovery,'candidates',lambda *a:[('first','','old'),('second','','old')])
    seen=[]
    net._compile=lambda p,h,s,f:seen.append(s) or SimpleNamespace(ok=False)
    recovery.run(net,'game',30,1,log);recovery.run(net,'game',30,1,log)
    assert seen==['first','second']


def test_changed_inputs_and_busy_claim_are_not_adopted(monkeypatch):
    net,log=setup(monkeypatch)
    def changed(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='concurrent edit'")
        return SimpleNamespace(ok=True,obj=b'fixture')
    net._compile=changed
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'
    net,log=setup(monkeypatch)
    monkeypatch.setattr(net.permuter,'_hold',lambda *a,**k:False)
    assert recovery.run(net,'game',30,1,log)['attempts']==0


def test_restore_flag_cannot_bypass_nonexact_or_missing_frozen_context():
    from decompnet.network import Agent, NetworkError
    net,_,fn=prepared_net()
    with pytest.raises(NetworkError):
        net._record(fn,Agent(1,'autopilot',None),1,'bad',0,matched=False,restoring_exact=True)


def test_recovery_synchronizes_before_freezing_compile_inputs(monkeypatch):
    from contextlib import contextmanager
    net,log=setup(monkeypatch)
    entered=[]
    @contextmanager
    def judging(slug):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='pulled before compilation'")
        entered.append(True)
        try:yield
        finally:entered.pop()
    net.progress=SimpleNamespace(judging=judging)
    compile=net._compile
    def checked_compile(*args):
        assert entered
        assert net.store.one('SELECT best_source FROM functions')['best_source']=='pulled before compilation'
        return compile(*args)
    net._compile=checked_compile
    assert recovery.run(net,'game',30,1,log)['matched']==1


def _file_store(net, tmp_path):
    import sqlite3

    from decompnet.store import Store
    path=tmp_path/'recovery.db'
    destination=sqlite3.connect(path)
    net.store.conn.backup(destination);destination.close()
    net.store=Store(path)
    net.store.conn.execute('PRAGMA busy_timeout=1')
    return path


@pytest.mark.parametrize('blocking_phase',['compile','evaluate','record'])
def test_recovery_waits_for_external_writer_without_recompiling_or_replaying_record(monkeypatch,tmp_path,blocking_phase):
    import sqlite3
    net,log=setup(monkeypatch)
    path=_file_store(net,tmp_path)
    held,release=threading.Event(),threading.Event()
    def writer():
        conn=sqlite3.connect(path,isolation_level=None)
        conn.execute('BEGIN IMMEDIATE');held.set()
        assert release.wait(5)
        conn.execute('ROLLBACK');conn.close()
    worker=threading.Thread(target=writer)
    compile,evaluate,record=net._compile,net._evaluate,net._record
    calls=[]
    def block():
        worker.start();assert held.wait(5)
        threading.Timer(.25,release.set).start()
    def checked_compile(*args):
        calls.append(1)
        if blocking_phase=='compile':block()
        return compile(*args)
    def checked_evaluate(*args):
        result=evaluate(*args)
        if blocking_phase=='evaluate':block()
        return result
    def checked_record(*args,**kwargs):
        result=record(*args,**kwargs)
        if blocking_phase=='record':block()
        return result
    net._compile,net._evaluate,net._record=checked_compile,checked_evaluate,checked_record
    try:
        result=recovery.run(net,'game',30,1,log)
    finally:
        release.set();worker.join(5)
    assert result['matched']==1 and calls==[1]
    assert net.store.one('SELECT count(*) AS n FROM attempts')['n']==1
    assert not net.store.one('SELECT 1 FROM claims')


def test_busy_postcommit_twins_index_does_not_turn_a_saved_match_into_an_error(monkeypatch,tmp_path):
    import sqlite3

    from decompnet.app.twins import Twins
    net,log=setup(monkeypatch)
    path=_file_store(net,tmp_path)
    net._layout_key=lambda proj:'stable'
    net.cfg=lambda proj:json.loads(proj['config'])
    twins=Twins(net)
    monkeypatch.setattr(twins,'prints',lambda proj:{1:SimpleNamespace(key='empty-return',size=4,asm=False)})
    blocker=sqlite3.connect(path,isolation_level=None)
    def on_match(proj,fid,agent):
        blocker.execute('BEGIN IMMEDIATE')
        try:twins.index_function(proj,fid)
        finally:blocker.execute('ROLLBACK')
    net.on_match=on_match
    twins._index_stamps[1]='old'
    try:result=recovery.run(net,'game',30,1,log)
    finally:blocker.close()
    assert result['matched']==1 and net.store.one('SELECT status FROM functions')['status']=='matched'
    assert 1 not in twins._index_stamps
    assert not net.store.one('SELECT 1 FROM claims')
    twins.index_project(net.project_row('game'))
    assert net.store.one('SELECT function_id FROM cross_game_index')['function_id']==1


@pytest.mark.parametrize('change',['save_metadata','compiler_flags','unknown_build_input'])
def test_recovery_ignores_save_bookkeeping_but_rejects_real_build_changes(monkeypatch,change):
    net,log=setup(monkeypatch)
    compile=net._compile
    def changed(*args):
        cfg=json.loads(net.project_row('game')['config'])
        if change=='save_metadata':cfg.update(progress_revision=100,progress_folder='/saved',save_folder='/backup',instructions='new note',priority=[1])
        elif change=='compiler_flags':cfg['compiler']['flags']=['-O3']
        else:cfg['unknown_build_input']='changed'
        with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        return compile(*args)
    net._compile=changed
    result=recovery.run(net,'game',30,1,log)
    assert result['matched']==int(change=='save_metadata')
    assert net.store.one('SELECT status FROM functions')['status']==('matched' if change=='save_metadata' else 'open')
    assert not net.store.one('SELECT 1 FROM claims')


def test_cancelling_recovery_while_waiting_for_a_writer_keeps_work_and_releases_claim(monkeypatch,tmp_path):
    import sqlite3
    net,log=setup(monkeypatch)
    path=_file_store(net,tmp_path)
    held,release=threading.Event(),threading.Event()
    def writer():
        conn=sqlite3.connect(path,isolation_level=None)
        conn.execute('BEGIN IMMEDIATE');held.set()
        assert release.wait(5)
        conn.execute('ROLLBACK');conn.close()
    worker=threading.Thread(target=writer)
    compile=net._compile
    def changed(*args):
        worker.start();assert held.wait(5)
        log.cancel_event.set()
        threading.Timer(.25,release.set).start()
        return compile(*args)
    net._compile=changed
    try:result=recovery.run(net,'game',30,1,log)
    finally:release.set();worker.join(5)
    assert result['matched']==0 and net.store.one('SELECT status FROM functions')['status']=='open'
    assert not net.store.one('SELECT 1 FROM claims')


def test_real_empty_mips_function_is_recovered_through_the_ordinary_exact_judge(tmp_path,monkeypatch):
    from decompnet import toolchains
    from decompnet.app.engine import RomNetwork
    from decompnet.app.engine.common import BASE_HEADER
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('needs the pinned period MIPS compiler')
    target=bytes.fromhex('03e0000800000000')
    (tmp_path/'probe.z64').write_bytes(target)
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'gp':0,
         'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2']},
         'rom':{'file':'probe.z64','sha1':'homemade-empty-return'},
         'segments':[{'name':'code','rom_start':0,'rom_end':8,'vram':0x80001000}]}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                       ('game','Empty demo',str(tmp_path),'mips',1,json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO context_versions(project_id,version,header) VALUES(?,?,?)',(pid,1,BASE_HEADER.format(title='Empty demo')))
        fid=db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm) VALUES(?,?,?,?,?,?,?,?,?)',
                       (pid,'empty_return',0x80001000,0,8,2,target,'[]','glabel empty_return\n/* 0 80001000 03E00008 */ jr $ra\n/* 4 80001004 00000000 */ nop')).lastrowid
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'empty_return',0x80001000,8,'func'))
    monkeypatch.setattr(net,'on_match',lambda *args:None)
    agent,_=net.local_agent('autopilot')
    assert net.attempt(agent,'empty_return','void empty_return(void) {}','game')['matched']
    net.progress.sync('game',force=True)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_source=NULL,best_score=NULL WHERE id=?",(fid,))
    net.progress.sync('game',force=True)
    log=lambda *args:None
    result=recovery.run(net,'game',30,4,log)
    assert result['matched']==1 and net._function('empty_return','game')['status']=='matched'
    assert net.store.one('SELECT via FROM attempts ORDER BY id DESC LIMIT 1')['via']=='saved-match-reconciliation'
    assert not net.store.one('SELECT 1 FROM claims')
