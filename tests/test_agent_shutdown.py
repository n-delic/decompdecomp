"""Shutdown signals every worker before persistence, and checkpoints/releases in bounded batches."""
import io
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import agent_shutdown, agents, collab
from decompnet.app.engine import RomNetwork
from decompnet.app.shutdown_progress import Progress
from decompnet.app.support import Jobs
from tests.test_loading import tiny_game


@pytest.fixture
def manager(tmp_path,monkeypatch):
    monkeypatch.setattr(agents.Manager,'_strays',lambda self:set())
    monkeypatch.setattr(agents.Manager,'refresh_detected',lambda self:None)
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    tiny_game(net)
    return agents.Manager(net,tmp_path,'http://127.0.0.1:1/mcp')


def helper(manager,name,index):
    actor,_=manager.net.local_agent(name)
    session=manager._record('game','codex',name,name,{'api_key':'private'})
    session.state='working';session.tokens=index
    manager._save(session)
    return actor,session


def test_all_processes_are_signalled_without_waiting_for_database_or_transcript(manager,monkeypatch):
    sessions=[helper(manager,'worker-'+str(i),i)[1] for i in range(3)]
    killed=[]
    for i,s in enumerate(sessions):
        s.proc=SimpleNamespace(pid=1000+i,poll=lambda:None)
        s.record=lambda *args:pytest.fail('signal phase must not write transcripts')
    monkeypatch.setattr(agents,'_terminate',lambda proc:killed.append(proc.pid))
    monkeypatch.setattr(manager.net.store,'tx',lambda **kwargs:pytest.fail('signal phase must not acquire database locks'))
    manager.request_shutdown()
    assert killed==[1000,1001,1002]
    assert all(s.stop.is_set() and s.state=='stopped' for s in sessions)
    manager.request_shutdown()
    assert len(killed)==3


def test_recovery_is_saved_before_claim_release_and_other_workers_keep_claims(manager,monkeypatch):
    net=manager.net
    actor,s=helper(manager,'worker',123)
    other,_=net.local_agent('remote')
    fn=net._function('func_0','game');otherfn=net._function('func_1','game')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],actor.id,1,9999999999))
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(otherfn['id'],other.id,1,9999999999))
    calls=[]
    monkeypatch.setattr(manager,'_release',lambda *args:pytest.fail('shutdown must not do per-helper claim scans'))
    manager.on_stop=lambda:calls.append('rooms')
    manager.shutdown(notify=calls.append)
    saved=net.store.one('SELECT state,stopped_by,options,tokens FROM helpers WHERE id=?',s.id)
    assert saved['state']=='stopped' and saved['stopped_by']=='quit' and saved['tokens']==123
    options=json.loads(saved['options'])
    assert options['resume_functions']==['func_0'] and 'api_key' not in options
    assert [r['agent_id'] for r in net.store.all('SELECT agent_id FROM claims')]==[other.id]
    assert net.store.one("SELECT COUNT(*) n FROM notes WHERE agent_id=? AND text LIKE 'Released because%'",actor.id)['n']==1
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='freed' AND agent_id=?",actor.id)['n']==1
    assert calls[0]=='rooms'
    assert next(i for i,c in enumerate(calls) if c.startswith('Saving recovery')) < next(i for i,c in enumerate(calls) if c.startswith('Releasing'))
    before=len(calls);manager.shutdown(notify=calls.append);assert len(calls)==before


def test_many_idle_helpers_do_not_create_run_boundaries_in_unrelated_projects(manager):
    net=manager.net
    for i in range(40):helper(manager,'idle-'+str(i),i)
    with net.store.tx() as db:
        db.executemany("INSERT INTO projects(slug,root,config) VALUES(?,?,'{}')",[(f'other-{i}','') for i in range(20)])
    manager.shutdown()
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='freed'")['n']==40
    assert not net.store.one("SELECT 1 FROM events e JOIN projects p ON p.id=e.project_id WHERE p.slug LIKE 'other-%'")


def test_expired_recovery_deadline_leaves_claims_for_startup(manager):
    actor,s=helper(manager,'worker',1)
    fn=manager.net._function('func_0','game')
    with manager.net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],actor.id,1,9999999999))
    manager.request_shutdown()
    with pytest.raises(TimeoutError):agent_shutdown.persist(manager,time.monotonic()-1,lambda _:None)
    assert manager.net.store.one('SELECT 1 FROM claims WHERE agent_id=?',actor.id)
    assert manager.net.store.one('SELECT state FROM helpers WHERE id=?',s.id)['state']=='working'


def test_slow_cleanup_does_not_prevent_later_shutdown_stages_or_print_late_phases():
    release=threading.Event();ended=threading.Event();terminal=io.StringIO()
    def blocked(notify):
        release.wait(2)
        notify('late phase must not appear')
        ended.set()
    with Progress(logging.getLogger('test.shutdown'),stream=terminal,interval=.01) as status:
        try:
            assert not status.bounded_step('Saving recovery',blocked,.02)
            assert status.bounded_step('Next stage',lambda _:None,1)
            release.set();assert ended.wait(1)
        finally:release.set()
    assert 'Timed out while saving recovery' in terminal.getvalue()
    assert 'Next stage complete' in terminal.getvalue() and 'late phase must not appear' not in terminal.getvalue()


def test_jobs_reject_starts_and_signal_existing_work_before_cleanup():
    jobs=Jobs();entered=threading.Event();done=threading.Event()
    def work(log):
        entered.set();log.cancel_event.wait(1);done.set()
    jobs.start('test',work);assert entered.wait(1)
    jobs.shutdown();assert done.wait(1)
    with pytest.raises(ValueError,match='shutting down'):jobs.start('later',lambda _:None)


def test_room_admission_stops_before_authentication_or_database_work(manager,monkeypatch):
    from starlette.testclient import TestClient
    net=manager.net;net.shutting_down=True
    monkeypatch.setattr(net,'authenticate',lambda *args:pytest.fail('shutdown must reject before DB authentication'))
    response=TestClient(collab.host_app(net,{})).get('/collab/state?room=game')
    assert response.status_code==400 and 'shutting down' in response.json()['error']


def test_batched_shutdown_uses_the_project_owner_for_claims_and_notes(tmp_path):
    # Reuse the independent-file fixture without enabling a runtime storage switch.
    from tests.test_project_storage import scoped
    fixture=scoped.__wrapped__(tmp_path)
    net,stores=next(fixture)
    try:
        for pid in (1,2):
            with stores[pid].tx() as db:db.execute('INSERT INTO claims VALUES(?,1,1,9999999999)',(pid*10,))
        session=SimpleNamespace(id='1',agent_name='worker',slug='first',options={})
        saved=[]
        manager=SimpleNamespace(net=net,_shutdown_sessions=[session],_shutdown_live={'1'},
                                _save=lambda s,db:saved.append(dict(s.options)))
        agent_shutdown.persist(manager,time.monotonic()+10,lambda _:None)
        assert saved==[{'resume_functions':['same_name']}]
        assert not net.store.one('SELECT 1 FROM notes')
        for pid in (1,2):
            assert not stores[pid].one('SELECT 1 FROM claims')
            assert stores[pid].one("SELECT COUNT(*) n FROM events WHERE kind='freed'")['n']==1
            assert stores[pid].one('SELECT COUNT(*) n FROM notes')['n']==1
    finally:
        with pytest.raises(StopIteration):next(fixture)


def test_busy_database_gate_obeys_recovery_deadline_without_dropping_checkpoint(manager):
    actor,s=helper(manager,'busy-worker',9)
    manager.request_shutdown()
    ended=threading.Event();errors=[]
    def persist():
        try:agent_shutdown.persist(manager,time.monotonic()+.05,lambda _:None)
        except Exception as error:errors.append(error)
        finally:ended.set()
    with manager.net.store.writer_gate.hold():
        worker=threading.Thread(target=persist)
        worker.start()
        assert ended.wait(2)
    worker.join(1)
    assert len(errors)==1 and isinstance(errors[0],TimeoutError)
    assert manager.net.store.one('SELECT state FROM helpers WHERE id=?',s.id)['state']=='working'


def test_job_reserved_during_shutdown_is_cancelled_before_its_body(monkeypatch):
    jobs=Jobs();entered=threading.Event();release=threading.Event();identities=[];body=[]
    original=jobs.check_start
    def check_start():
        original();entered.set();release.wait(2)
    monkeypatch.setattr(jobs,'check_start',check_start)
    reservation=threading.Thread(target=lambda:identities.append(jobs.start('race',lambda _:body.append(True))))
    reservation.start();assert entered.wait(1)
    jobs.shutdown();release.set();reservation.join(2)
    for _ in range(100):
        if jobs.jobs[identities[0]]['state']!='running':break
        time.sleep(.01)
    assert not body and jobs.jobs[identities[0]]['state']=='cancelled'


def test_quit_exits_after_blocked_recovery_without_racing_a_final_folder_sync(tmp_path,monkeypatch):
    from decompnet.app import server, window
    monkeypatch.setattr(agents.Manager,'refresh_detected',lambda self:None)
    monkeypatch.setattr(agents.Manager,'_strays',lambda self:set())
    home=tmp_path/'.decomp';home.mkdir()
    net=RomNetwork(home/'test.db',tmp_path)
    monkeypatch.setattr(window,'WINDOW',None)
    _,manager,_,quit_app=server.build(net,home,6496,'test-secret')
    release=threading.Event();ended=threading.Event();output=io.StringIO();exited=[]
    original=manager.shutdown
    releases=[];gate=threading.Barrier(2)
    def remote_release(slug,*args):
        assert all(link.stopped.is_set() for link in net.links.values())
        gate.wait(timeout=1)
        releases.append((slug,args))
    for slug in ('room-one','room-two'):
        stopped=threading.Event()
        net.links[slug]=SimpleNamespace(slug=slug,stopped=stopped,stop=stopped.set,
                                       _req=lambda *args,slug=slug:remote_release(slug,*args))
    def blocked(**kwargs):
        kwargs['notify']('Saving recovery for the blocked project')
        release.wait(2);kwargs['notify']('late phase');ended.set()
    class FastProgress(Progress):
        def bounded_step(self,label,action,timeout):
            return super().bounded_step(label,action,min(timeout,.1))
    monkeypatch.setattr(server,'ShutdownProgress',lambda logger:FastProgress(logger,stream=output,interval=.01))
    monkeypatch.setattr(manager,'shutdown',blocked)
    monkeypatch.setattr(net.progress,'flush',lambda **kwargs:pytest.fail('incomplete cleanup must not race a final sync'))
    monkeypatch.setattr(window,'close_app',lambda:None)
    monkeypatch.setattr(server.wine,'stop',lambda:None)
    monkeypatch.setattr(server.os,'_exit',lambda code:exited.append(code))
    try:
        quit_app()
        assert exited==[0] and manager._down and net.shutting_down
        assert sorted(releases)==[('room-one',('POST','/collab/leave')),('room-two',('POST','/collab/leave'))]
        assert 'Deferring final progress-folder sync' in output.getvalue()
        assert 'blocked project' in output.getvalue()
        release.set();assert ended.wait(1)
        assert 'late phase' not in output.getvalue()
    finally:
        release.set();original()
