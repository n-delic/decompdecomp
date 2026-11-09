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


@pytest.mark.parametrize("count", [1, 4])
def test_real_empty_mips_function_is_recovered_through_the_ordinary_exact_judge(tmp_path,monkeypatch,count):
    from decompnet import toolchains
    from decompnet.app.engine import RomNetwork
    from decompnet.app.engine.common import BASE_HEADER
    if toolchains.installed('gcc-2.7.2-kmc') is None:
        pytest.skip('needs the pinned period MIPS compiler')
    target=bytes.fromhex('03e0000800000000')
    (tmp_path/'probe.z64').write_bytes(target*count)
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'gp':0,
         'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2']},
         'rom':{'file':'probe.z64','sha1':'homemade-empty-return'},
         'segments':[{'name':'code','rom_start':0,'rom_end':8*count,'vram':0x80001000}]}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',
                       ('game','Empty demo',str(tmp_path),'mips',1,json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO context_versions(project_id,version,header) VALUES(?,?,?)',(pid,1,BASE_HEADER.format(title='Empty demo')))
        fid=db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm) VALUES(?,?,?,?,?,?,?,?,?)',
                       (pid,'empty_return',0x80001000,0,8,2,target,'[]','glabel empty_return\n/* 0 80001000 03E00008 */ jr $ra\n/* 4 80001004 00000000 */ nop')).lastrowid
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'empty_return',0x80001000,8,'func'))
    monkeypatch.setattr(net,'on_match',lambda *args:None)
    agent,_=net.local_agent('autopilot')
    for index in range(1, count):
        name=f'empty_return_{index}'
        with net.store.tx() as db:
            db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm) '
                       'SELECT project_id,?,vram+?,rom_offset+?,size,n_insns,target,relocs,asm FROM functions WHERE id=?',
                       (name, index*8, index*8, fid))
            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                       (pid,name,0x80001000+index*8,8,'func'))
    for index in range(count):
        name='empty_return' if index == 0 else f'empty_return_{index}'
        assert net.attempt(agent,name,f'void {name}(void) {{}}','game')['matched']
    net.progress.sync('game',force=True)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,best_source=NULL,best_score=NULL")
    net.progress.sync('game',force=True)
    log=lambda *args:None
    if count > 1:
        # A large open queue mostly has no successful history. Preliminary scanning
        # must not invoke Core._project's filesystem synchronization for these rows.
        with net.store.tx() as db:
            for index in range(64):
                db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs) '
                           'SELECT project_id,?,vram+?,rom_offset+?,size,n_insns,target,relocs FROM functions WHERE id=?',
                           (f'untried_{index}', (count+index)*8, (count+index)*8, fid))
        net.progress.sync('game', force=True)
        lookup=net.project_row
        edits=[]
        def dirty_project(slug):
            proj=lookup(slug)
            edits.append(1)
            with net.store.tx() as db:
                db.execute("UPDATE functions SET best_source=?,best_score=1 WHERE name='untried_0'",
                           (f'void untried_0(void) {{ /* edit {len(edits)} */ }}',))
            return proj
        monkeypatch.setattr(net, 'project_row', dirty_project)
    sync=net.progress.sync
    implicit=[]
    def tracked_sync(slug, **kwargs):
        if not kwargs.get('force') and not net.progress._batching(slug) and not net.progress.applying:
            implicit.append(slug)
        return sync(slug, **kwargs)
    monkeypatch.setattr(net.progress, 'sync', tracked_sync)
    result=recovery.run(net,'game',None,None,log)
    assert len(implicit) == 2  # initial validation only, never preliminary per-function reads
    assert not net.progress.viewing
    assert result['matched']==count and net._function('empty_return','game')['status']=='matched'
    assert net.store.one('SELECT via FROM attempts ORDER BY id DESC LIMIT 1')['via']=='saved-match-reconciliation'
    assert not net.store.one('SELECT 1 FROM claims')


def test_recovery_bank_indexes_addresses_without_losing_overlay_identity(monkeypatch):
    net, proj, fn = prepared_net()
    source = 'int old(void){return 1;}'
    proof = dict(verdict='exact', source_sha256=match_bundle.digest(source),
                 abi=match_bundle.abi(proj, json.loads(proj['config'])), vram=fn['vram'],
                 target_sha256=match_bundle.digest(fn['target'] or b''),
                 relocations_sha256=match_bundle.digest(fn['relocs'] or ''),
                 header_sha256=match_bundle.digest('private'))
    with net.store.tx() as db:
        db.execute(match_bundle.SCHEMA)
        db.execute('INSERT INTO verified_matches VALUES(?,?,?,?,?)',
                   (fn['id'], -1, json.dumps(proof), source, 'private'))
    indexed = recovery.bank(net, proj)
    assert list(indexed) == [fn['vram']]
    expected = recovery.candidates(net, proj, fn, indexed[fn['vram']])
    assert recovery.candidates(net, proj, fn, indexed) == expected
    # A different overlay can share an address, but its different target still rejects the donor.
    changed = dict(fn, target=b'different overlay')
    assert recovery.candidates(net, proj, changed, indexed) == recovery.candidates(net, proj, changed, [])


def add_functions(net, count):
    with net.store.tx() as db:
        for index in range(1, count):
            db.execute('INSERT INTO functions(project_id,name,unit,vram,size,n_insns,target,relocs,meta) '
                       'SELECT project_id,?,unit,vram+?,size,n_insns,target,relocs,meta FROM functions WHERE id=1',
                       (f'f{index}', index*16))


def test_compiles_overlap_but_exact_judgments_and_records_stay_on_coordinator(monkeypatch):
    from contextlib import contextmanager

    from decompnet.app import workers
    net, log = setup(monkeypatch)
    add_functions(net, 4)
    monkeypatch.setattr(workers, 'WORKERS', 2)
    monkeypatch.setattr(workers, 'pool', lambda: pytest.fail('shared workers may wait for our project lock'))
    barrier = threading.Barrier(2)
    coordinator = threading.get_ident()
    compilers, judges, records, syncs, publications = [], [], [], [], []
    compile, evaluate, record = net._compile, net._evaluate, net._record
    @contextmanager
    def judging(slug):
        syncs.append(slug)
        yield
    @contextmanager
    def batch(slug):
        publications.append(slug)
        yield
    net.progress = SimpleNamespace(judging=judging, batch=batch)
    def parallel(*args):
        compilers.append(threading.get_ident())
        barrier.wait(5)
        return compile(*args)
    def serial_judge(*args):
        judges.append(threading.get_ident())
        return evaluate(*args)
    def serial_record(*args, **kwargs):
        records.append(threading.get_ident())
        return record(*args, **kwargs)
    net._compile, net._evaluate, net._record = parallel, serial_judge, serial_record
    updates = []
    log.progress = lambda p: updates.append(p)
    result = recovery.run(net, 'game', 30, 4, log)
    assert result['matched'] == result['attempts'] == 4
    assert coordinator not in compilers and len(set(compilers)) == 2
    assert judges == records == [coordinator]*4
    assert len(syncs) == 3 and len(publications) == 2
    assert any(p['matched'] and p['matches_per_minute'] > 0 for p in updates)
    assert not net.store.one('SELECT 1 FROM claims')


def test_no_history_functions_do_not_force_progress_sync_or_candidate_scope(monkeypatch):
    from contextlib import contextmanager
    net, log = setup(monkeypatch)
    add_functions(net, 100)
    monkeypatch.setattr(recovery, 'candidates', lambda *a: [])
    monkeypatch.setattr(recovery.candidate_scope, 'key', lambda *a: pytest.fail('empty pool needs no scope'))
    syncs = []
    @contextmanager
    def judging(slug):
        syncs.append(slug)
        yield
    net.progress = SimpleNamespace(judging=judging)
    result = recovery.run(net, 'game', 30, 20, log)
    assert result['attempts'] == result['matched'] == 0
    assert len(syncs) == 1


def test_compile_budget_bounds_submission_and_all_reserved_results_are_judged(monkeypatch):
    from decompnet.app import workers
    net, log = setup(monkeypatch)
    add_functions(net, 10)
    monkeypatch.setattr(workers, 'WORKERS', 8)
    called = []
    compile = net._compile
    def count(*args):
        called.append(args[-1]['id'])
        return compile(*args)
    net._compile = count
    result = recovery.run(net, 'game', 30, 3, log)
    assert result['attempts'] == result['matched'] == len(called) == 3
    assert not net.store.one('SELECT 1 FROM claims')


def test_later_candidate_releases_batch_and_keeps_fresh_snapshot(monkeypatch):
    from contextlib import contextmanager
    net, log = setup(monkeypatch)
    monkeypatch.setattr(recovery, 'candidates', lambda *a: [('first','','old'),('second','','old')])
    seen, batches = [], []
    compile = net._compile
    def different(*args):
        seen.append(args[2])
        return SimpleNamespace(ok=False) if args[2] == 'first' else compile(*args)
    @contextmanager
    def batch(slug):
        batches.append(slug)
        yield
    @contextmanager
    def judging(slug): yield
    net.progress = SimpleNamespace(batch=batch, judging=judging)
    net._compile = different
    result = recovery.run(net, 'game', 30, 4, log)
    assert result['matched'] == 1 and seen == ['first','second']
    assert len(batches) == 2
    assert not net.store.one('SELECT 1 FROM claims')


def test_parallel_results_reject_function_changed_after_submission(monkeypatch):
    from decompnet.app import workers
    net, log = setup(monkeypatch)
    add_functions(net, 2)
    monkeypatch.setattr(workers, 'WORKERS', 2)
    barrier = threading.Barrier(2)
    compile = net._compile
    def changed(*args):
        if args[-1]['id'] == 2:
            with net.store.tx() as db:db.execute("UPDATE functions SET best_source='concurrent edit' WHERE id=2")
        barrier.wait(5)
        return compile(*args)
    net._compile = changed
    result = recovery.run(net, 'game', 30, 2, log)
    assert result['matched'] == 1
    assert net.store.one('SELECT status FROM functions WHERE id=2')['status'] == 'open'
    assert not net.store.one('SELECT 1 FROM claims')


def test_cancelling_parallel_wave_discards_results_and_releases_every_claim(monkeypatch):
    from decompnet import compiler
    from decompnet.app import workers
    net, log = setup(monkeypatch)
    add_functions(net, 2)
    monkeypatch.setattr(workers, 'WORKERS', 2)
    barrier = threading.Barrier(2)
    ended = threading.Event()
    def cancelled(*args):
        barrier.wait(5)
        log.cancel_event.set()
        try:compiler._CANCEL_CHECK.get()()
        finally:ended.set()
    net._compile = cancelled
    result = recovery.run(net, 'game', None, None, log)
    assert ended.wait(5)
    assert result['matched'] == 0 and result['attempts'] == 2
    assert not net.store.one("SELECT 1 FROM functions WHERE status='matched'")
    assert not net.store.one('SELECT 1 FROM claims')


def test_deadline_rejects_finished_compile_before_promoting(monkeypatch):
    net, log = setup(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: now[0])
    compile = net._compile
    def late(*args):
        built = compile(*args)
        now[0] = 2
        return built
    net._compile = late
    result = recovery.run(net, 'game', 1, 1, log)
    assert result['matched'] == 0
    assert net.store.one('SELECT status FROM functions')['status'] == 'open'
    assert not net.store.one('SELECT 1 FROM claims')


@pytest.mark.parametrize('seconds,attempts', [(None,None), (None,1), (1,None), (600,1024)])
def test_optional_limits_validate_independently(monkeypatch, seconds, attempts):
    net, _ = setup(monkeypatch)
    assert recovery.validate(net, 'game', seconds, attempts)['id'] == 1


@pytest.mark.parametrize('seconds,attempts', [(0,None), (601,None), (True,None), ('30',None),
                                           (None,0), (None,1025), (None,False), (None,1.5)])
def test_explicit_invalid_limits_are_rejected(monkeypatch, seconds, attempts):
    net, _ = setup(monkeypatch)
    with pytest.raises(ValueError, match='Leave limits blank'):
        recovery.validate(net, 'game', seconds, attempts)


def test_unlimited_pass_exhausts_candidates_beyond_previous_defaults(monkeypatch):
    net, log = setup(monkeypatch)
    add_functions(net, 130)
    now = [0.0]
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: now[0])
    compile = net._compile
    def advancing(*args):
        now[0] += 10
        return compile(*args)
    net._compile = advancing
    result = recovery.run(net, 'game', None, None, log)
    assert result['matched'] == result['attempts'] == 130
    assert result['seconds'] > 600
    assert not net.store.one("SELECT 1 FROM functions WHERE status='open'")
    assert not net.store.one('SELECT 1 FROM claims')


def test_unlimited_pass_stops_after_each_failed_candidate_once(monkeypatch):
    net, log = setup(monkeypatch)
    monkeypatch.setattr(recovery, 'candidates', lambda *a: [('first','','old'), ('second','','old')])
    seen = []
    net._compile = lambda p,h,s,f: seen.append(s) or SimpleNamespace(ok=False)
    result = recovery.run(net, 'game', None, None, log)
    assert result['attempts'] == 2 and result['matched'] == 0
    assert seen == ['first','second']
    assert not net.store.one('SELECT 1 FROM claims')


def test_history_bank_includes_old_matches_beyond_former_global_cap(monkeypatch):
    net, proj, _ = prepared_net()
    add_functions(net, 4100)
    source = 'void f(void) {}'
    detail = dict(verdict='exact', source_sha256=match_bundle.digest(source), vram=4096)
    with net.store.tx() as db:
        db.execute(match_bundle.SCHEMA)
        db.execute('INSERT INTO verified_matches SELECT id,id,?,?,? FROM functions',
                   (json.dumps(detail),source,''))
    records = recovery.bank(net, proj)
    assert len(records[4096]) == 4100
    assert records[4096][-1][0]['name'] == 'f'


def test_empty_recovery_pool_does_not_decode_the_current_header(monkeypatch):
    net, proj, fn = prepared_net()
    with net.store.tx() as db:db.execute('DELETE FROM attempts')
    fn = dict(fn, matched_source=None)
    monkeypatch.setattr(net, '_context', lambda *a: pytest.fail('no historical candidate needs a current header'))
    assert recovery.candidates(net, proj, fn, {}) == []
