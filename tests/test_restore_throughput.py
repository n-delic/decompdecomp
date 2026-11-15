"""Restore verifies promptly; optional derived work and private compiler setup are coalesced."""
import logging
import threading
from types import SimpleNamespace

import pytest

from decompnet import compile_sessions
from decompnet.app import restore_compilation, restore_effects, workers
from decompnet.app.engine import RomNetwork
from decompnet.app.startup import StartupQueue


class Log:
    def check_cancel(self):pass
    def progress(self,value):pass
    def __call__(self,value):pass


@pytest.fixture
def setup(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,'{}')",(str(tmp_path),)).lastrowid
        for name in ('a','b'):
            db.execute("INSERT INTO functions(project_id,name,status,size) VALUES(?,?,'matched',4)",(pid,name))
    calls=[];queued=[]
    net.startup=SimpleNamespace(add=lambda label,fn,**scope:queued.append((label,fn,scope)),start=lambda:None)
    monkeypatch.setattr(net.twins,'index_project',lambda *a:calls.append('index'))
    monkeypatch.setattr(net.twins,'after_match',lambda p,fid:calls.append(('twin',fid)))
    monkeypatch.setattr('decompnet.app.repair_mappings.harvest',lambda twins,p,fid:calls.append(('mapping',fid)))
    net.match_library=SimpleNamespace(archive_project=lambda p,log,rows:calls.append(('archive',len(rows))))
    monkeypatch.setattr(net.cascades,'notify_many',lambda p,ids:calls.append(('cascade',set(ids))))
    return net,net._project('game'),calls,queued


def test_bulk_restore_returns_before_slow_optional_services_and_coalesces_work(setup):
    net,proj,calls,queued=setup
    @restore_effects.batched
    def restore(net):
        net.on_match(proj,1)
        net.on_match(proj,2)
        net.on_match(proj,1)
        assert calls==[] and queued==[]
        return 'verified'
    assert restore(net)=='verified'
    assert len(queued)==1 and queued[0][2]['project']=='game'
    queued[0][1](Log())
    assert calls.count('index')==1 and calls.count(('archive',2))==1
    assert calls[-1]==('cascade',{1,2})
    assert {c for c in calls if isinstance(c,tuple) and c[0]=='mapping'}=={('mapping',1),('mapping',2)}


def test_cancelled_restore_does_not_queue_maintenance_for_discarded_import(setup):
    net,proj,calls,queued=setup
    @restore_effects.batched
    def cancelled(net):
        net.on_match(proj,1)
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):cancelled(net)
    assert not calls and not queued
    assert not restore_effects.defer(net,proj,1,matched=True)


def test_deferred_task_rereads_current_matches_and_obeys_stop(setup):
    net,proj,calls,queued=setup
    @restore_effects.batched
    def restore(net):net.on_match(proj,1)
    restore(net)
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE id=1")
    queued[0][1](Log())
    assert 'index' not in calls and not any(isinstance(c,tuple) and c[0]=='archive' for c in calls)
    class Stop(Log):
        def check_cancel(self):raise RuntimeError('stop')
    calls.clear()
    with pytest.raises(RuntimeError,match='stop'):queued[0][1](Stop())
    assert not calls


def test_private_compiler_session_is_reused_and_closed_on_worker_exit(monkeypatch):
    monkeypatch.setattr(workers,'WORKERS',1)
    monkeypatch.setattr(restore_compilation.compiler,'concurrency_limit',lambda:4)
    assert restore_compilation.width()==4
    pool=restore_compilation.Pool(1);closed=[]
    def prepare():
        session=compile_sessions.current()
        assert session is not None
        session.stack.callback(lambda:closed.append(id(session)))
        return id(session),threading.get_ident()
    first=pool.submit(prepare).result(2)
    assert pool.submit(lambda:(id(compile_sessions.current()),threading.get_ident())).result(2)==first
    assert closed==[]
    pool.shutdown(wait=True)
    assert closed==[first[0]]


def test_optional_queue_restarts_after_drain_and_start_is_idempotent():
    queue=StartupQueue(logging.getLogger('restore-test'),delay=0,spacing=0,quiet=0)
    first,second,finish=threading.Event(),threading.Event(),threading.Event()
    queue.add('first',first.set);queue.start()
    assert first.wait(3)
    thread=queue._runner
    if thread:thread.join(3)
    def later():
        second.set();assert finish.wait(3)
    queue.add('later',later);queue.start();active=queue._runner;queue.start()
    assert queue._runner is active and second.wait(3)
    finish.set();active.join(3)
    assert all(t.state=='done' for t in queue.records.values())
    queue.close()


def test_optional_scheduler_failure_does_not_discard_verified_restore(setup):
    net,proj,_,_=setup
    def unavailable():raise RuntimeError('thread unavailable')
    net.startup.start=unavailable
    @restore_effects.batched
    def restore(net):
        net.on_match(proj,1)
        return 'verified'
    assert restore(net)=='verified'


def test_restore_summary_counts_do_not_decode_saved_source(setup):
    from decompnet.app.saves import _restore_counts
    net,proj,_,_=setup
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE id=1",('int body(void){return 0;} '*1000,))
        db.execute("UPDATE functions SET status='open',best_source=?,best_score=12 WHERE id=2",('int unfinished(void){return 1;} '*1000,))
    net.store._reader().create_function('payload_read',2,lambda *_:pytest.fail('summary decoded C'))
    assert _restore_counts(net,proj['slug'])=={'matched':1,'best':1,'lost':[]}
