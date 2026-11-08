"""User-started free matching runs on its own threads without the background CPU gate."""
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app import maintenance
from decompnet.app.engine import RomNetwork
from decompnet.app.engine.autopilot import AutopilotMixin
from decompnet.app.server import build
from decompnet.app.twins import Twins
from tests.test_app_server import PORT, SECRET, WINDOW
from tests.test_loading import tiny_game


def twin():
    value=Twins.__new__(Twins)
    value._lock=threading.Lock();value.jobs={}
    return value


def test_manual_full_pass_bypasses_busy_lane_on_a_dedicated_thread():
    value=twin();entered=threading.Event();ids=[]
    def execute(slug,job):
        assert maintenance.is_foreground()
        ids.append(threading.get_ident());job.state='done';entered.set()
        return job
    value._execute_full_pass=execute
    with maintenance.slot():
        worker=threading.Thread(target=lambda:value.full_pass('game',foreground=True))
        worker.start()
        try:assert entered.wait(1)
        finally:worker.join(1)
    assert ids and ids[0]!=threading.get_ident()


def test_click_promotes_one_queued_automatic_pass_without_duplicating_it(monkeypatch):
    value=twin();queued=threading.Event();entered=threading.Event();executions=[]
    original=maintenance.slot
    @contextmanager
    def slot(stop=None):
        if not maintenance.is_foreground():queued.set()
        with original(stop) as ready:yield ready
    monkeypatch.setattr(maintenance,'slot',slot)
    def execute(slug,job):
        assert maintenance.is_foreground()
        executions.append(job);job.state='done';entered.set();return job
    value._execute_full_pass=execute
    results=[]
    with original():
        owner=threading.Thread(target=lambda:results.append(value.full_pass('game')))
        owner.start();assert queued.wait(1)
        job=value.jobs['game']
        duplicate=threading.Thread(target=lambda:results.append(value.full_pass('game',foreground=True)))
        duplicate.start()
        try:assert entered.wait(1)
        finally:
            if not entered.is_set():job.stop.set();job.state='done'
            owner.join(2);duplicate.join(2)
    assert executions==[job] and results==[job,job]


@pytest.mark.parametrize('imported',[False,True])
def test_manual_autopilot_spawns_coordinator_and_compile_workers_while_lane_busy(tmp_path,monkeypatch,imported):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    tiny_game(net)
    monkeypatch.setattr('decompnet.app.engine.autopilot.seeds.available',lambda:True)
    phases=[];compiles=[];done=threading.Event()
    def execute(slug,job):
        phases.append((threading.get_ident(),maintenance.is_foreground()))
        job.state='done';return job
    monkeypatch.setattr(net.twins,'_execute_full_pass',execute)
    def compile_one(*args):
        compiles.append((threading.get_ident(),maintenance.is_foreground()))
        return 'far'
    monkeypatch.setattr(net,'_autopilot_one',compile_one)
    if imported:
        ids=[r['id'] for r in net.store.all('SELECT id FROM functions')]
        monkeypatch.setattr(net,'fork_slug',lambda slug:slug)
        net.forks=SimpleNamespace(autopilot_prepare=lambda slug:ids,autopilot_one=compile_one)
    original=net._autopilot_work
    def work(*args):
        try:return original(*args)
        finally:done.set()
    monkeypatch.setattr(net,'_autopilot_work',work)
    with maintenance.slot():
        state=net.start_autopilot('game',workers=2,foreground=True)
        assert done.wait(3)
        assert state['_foreground'].is_set()
    assert len(compiles)==4 and phases[0][1] and all(active for _,active in compiles)
    assert all(tid!=threading.get_ident() and tid!=phases[0][0] for tid,_ in compiles)
    assert '_foreground' not in net.autopilot_status('game')
    assert 'stop' not in net.autopilot_status('game')


def test_click_promotes_running_autopilot_state_and_keeps_automatic_default():
    promoted=[];event=threading.Event();state={'state':'running','_foreground':event}
    net=SimpleNamespace(autopilots={'game':state},cascades=SimpleNamespace(resume=lambda slug:None),
                        twins=SimpleNamespace(promote=promoted.append))
    assert AutopilotMixin.start_autopilot(net,'game') is state
    assert not event.is_set() and not promoted
    assert AutopilotMixin.start_autopilot(net,'game',foreground=True) is state
    assert event.is_set() and promoted==['game']


def test_explicit_autopilot_route_requests_foreground_workers(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    mcp,_,security,_=build(net,tmp_path,PORT,SECRET)
    net.autosave.close()
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    calls=[]
    monkeypatch.setattr(net,'start_autopilot',lambda slug,**kwargs:calls.append((slug,kwargs)))
    monkeypatch.setattr(net,'overview',lambda slug:{'slug':slug})
    response=client.post('/api/autopilot',json={'slug':'game'},headers={'X-Decomp':'1'})
    assert response.status_code==200 and calls==[('game',{'foreground':True})]


def test_promotion_before_twins_registration_is_not_lost():
    value=twin();entered=threading.Event();event=threading.Event()
    def execute(slug,job):
        assert job.foreground is event and maintenance.is_foreground()
        entered.set();job.state='done';return job
    value._execute_full_pass=execute
    # The user clicks just before the automatic coordinator registers its twins pass.
    value.promote('game')
    event.set()
    with maintenance.slot():
        worker=threading.Thread(target=lambda:value.full_pass('game',foreground_event=event))
        worker.start()
        try:assert entered.wait(1)
        finally:worker.join(1)


@pytest.mark.parametrize('promote', [False, True])
def test_default_manual_autopilot_uses_cores_and_restores_compile_limit(tmp_path, monkeypatch, promote):
    from decompnet import compiler
    from decompnet.compile_admission import Admission
    from decompnet.compile_sessions import current
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    tiny_game(net)
    monkeypatch.setattr('decompnet.app.engine.autopilot.os.cpu_count', lambda: 4)
    monkeypatch.setattr('decompnet.app.engine.autopilot.seeds.available', lambda: True)
    monkeypatch.setattr(compiler, '_SLOTS', Admission(2))
    monkeypatch.setattr(net.twins, 'full_pass', lambda *a, **kw: SimpleNamespace(matched=0))
    monkeypatch.setattr(net.twins, 'promote', lambda *a: None)
    release = threading.Event()
    three, four, finished = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    active = 0
    def one(*args):
        nonlocal active
        assert current() is not None
        with lock:
            active += 1
            if active == 3:three.set()
            if active == 4:
                assert compiler._SLOTS.limit >= 4
                four.set()
        assert release.wait(5)
        return 'far'
    monkeypatch.setattr(net, '_autopilot_one', one)
    original = net._autopilot_work
    def work(*args):
        try:return original(*args)
        finally:finished.set()
    monkeypatch.setattr(net, '_autopilot_work', work)
    state = net.start_autopilot('game', foreground=not promote)
    try:
        if promote:
            assert three.wait(3)
            assert not four.wait(.1), 'Automatic runs retain their three-worker ceiling'
            assert net.start_autopilot('game', foreground=True) is state
        assert four.wait(3), 'Manual runs must use four cores, including a promoted run'
    finally:
        release.set()
        assert finished.wait(5)
    assert state['tried'] == 4 and compiler._SLOTS.limit == 2
