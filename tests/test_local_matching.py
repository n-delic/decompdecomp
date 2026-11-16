"""Explicit queues resume safely, wait for claims, and never turn status reads into maintenance."""
import threading
import time

import pytest

from decompnet.app import deep_permuter
from decompnet.app.local_matching import Matching, options
from decompnet.app.support import Jobs
from decompnet.app.twins import Job
from decompnet.network import NetworkError

from .test_deep_permuter import Log, setup


def until(predicate, seconds=5):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        if value := predicate():
            return value
        time.sleep(.01)
    raise AssertionError('Search did not reach the expected state.')


def finish(jobs, result):
    job = until(lambda: jobs.jobs[result['job']] if jobs.jobs[result['job']]['state']!='running' else None)
    assert job['state']=='done',job
    return job


@pytest.mark.parametrize('values',[{'minutes':True},{'minutes':0},{'max_variants':0},{'threads':-1},{'loop':'yes'},{'repeat':1}])
def test_invalid_search_options_are_rejected(values):
    with pytest.raises(NetworkError):
        options(values)


def test_status_never_synchronizes_or_starts_a_project(tmp_path,monkeypatch):
    net = setup(tmp_path)
    matching = Matching(net,Jobs())
    def forbidden(*args,**kwargs):
        raise AssertionError('Status must only read stored metadata.')
    monkeypatch.setattr(net,'project_row',forbidden)
    monkeypatch.setattr(net.progress,'sync',forbidden)
    assert matching.status('game')['deep']['queue_left']==1
    assert matching.jobs.jobs=={}


@pytest.mark.parametrize('fork',[False,True])
def test_one_click_searches_all_eligible_work_in_both_modes(tmp_path,monkeypatch,fork):
    net = setup(tmp_path)
    monkeypatch.setattr(net,'fork_slug',lambda slug: 'build' if fork else None)
    with net.store.tx() as db:
        pid = db.execute('SELECT id FROM projects').fetchone()[0]
        db.executemany("INSERT INTO functions(project_id,name,n_insns,best_source,best_score) VALUES(?,?,10,'int f(void){return 1;}',40)",
                       [(pid,f'function_{i}') for i in range(105)])
    seen = []
    monkeypatch.setattr(deep_permuter,'run',lambda n,s,name,m,v,l:seen.append(name))
    jobs = Jobs();matching = Matching(net,jobs)
    job = finish(jobs,matching.start('game','deep',{'threads':1}))
    assert len(set(seen))==106
    assert job['scope']=={'project':'game'}
    assert matching.status('game')['deep']['queue_done']==106


def test_completed_rom_turns_resume_and_changed_headers_are_retried(tmp_path,monkeypatch):
    net = setup(tmp_path);calls = []
    def search(n,slug,name,*args):
        calls.append(name)
        with n.store.tx() as db:
            db.execute('UPDATE functions SET best_score=20')
    monkeypatch.setattr(deep_permuter,'run',search)
    jobs = Jobs();matching = Matching(net,jobs)
    finish(jobs,matching.start('game','deep'))
    assert matching.status('game')['deep']['improved']==1
    assert matching.status('game')['deep']['queue_left']==0
    resumed = Matching(net,Jobs())
    finish(resumed.jobs,resumed.start('game','deep'))
    assert len(calls)==1
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='changed'")
    finish(resumed.jobs,resumed.start('game','deep'))
    assert len(calls)==2
    resumed.reset('game')
    assert resumed.status('game')['deep']['improved']==0
    assert resumed.status('game')['deep']['queue_done']==1


def test_input_change_during_a_turn_does_not_complete_the_new_inputs(tmp_path,monkeypatch):
    net = setup(tmp_path);matching = Matching(net,Jobs())
    def search(*args):
        with net.store.tx() as db:
            db.execute("UPDATE context_versions SET header='changed during search'")
    monkeypatch.setattr(deep_permuter,'run',search)
    fn = net.store.one('SELECT * FROM functions');project = matching.project('game')
    assert matching._search('game',project,fn,options({}),Log())=='pending'
    assert matching.status('game')['deep']['queue_left']==1


def test_claimed_work_waits_and_duplicate_clicks_attach_to_the_same_job(tmp_path,monkeypatch):
    net = setup(tmp_path);entered = threading.Event()
    agent,_ = net.local_agent('manual-test')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(1,agent.id,time.time(),time.time()+60))
    monkeypatch.setattr(deep_permuter,'run',lambda *args:entered.set())
    jobs = Jobs();matching = Matching(net,jobs)
    first = matching.start('game','deep')
    until(lambda:(jobs.jobs[first['job']]['progress'] or {}).get('phase')=='Waiting for available functions')
    assert matching.start('game','deep')==first
    assert not entered.is_set()
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    finish(jobs,first)
    assert entered.is_set()


def test_cancellation_preserves_improvements_and_unfinished_turns(tmp_path,monkeypatch):
    net = setup(tmp_path);entered = threading.Event()
    def search(n,slug,name,minutes,variants,log):
        with n.store.tx() as db:
            db.execute('UPDATE functions SET best_score=20')
        entered.set()
        while True:
            log.check_cancel();time.sleep(.01)
    monkeypatch.setattr(deep_permuter,'run',search)
    jobs = Jobs();matching = Matching(net,jobs)
    result = matching.start('game','deep')
    assert entered.wait(5)
    with pytest.raises(NetworkError,match='Stop'):
        matching.reset('game')
    matching.stop('game','deep')
    job = until(lambda:jobs.jobs[result['job']] if jobs.jobs[result['job']]['state']!='running' else None)
    assert job['state']=='cancelled'
    assert 'Search stopped' in job['error']
    assert matching.status('game')['deep']['improved']==1
    assert matching.status('game')['deep']['queue_left']==1


def test_cross_search_waits_and_saves_source_links_without_source_code(tmp_path,monkeypatch):
    net = setup(tmp_path);busy = Job();net.twins.jobs['game'] = busy
    monkeypatch.setattr(net,'autopilot_status',lambda slug:None)
    def cross(slug,check):
        check.tried = 1
        check.report({'target':'f','source':'donor','source_slug':'another-game','game':'Another game','outcome':'match','reason':''})
        return 1
    monkeypatch.setattr(net.twins,'across_games',cross)
    jobs = Jobs();matching = Matching(net,jobs)
    result = matching.start('game','cross')
    until(lambda:jobs.jobs[result['job']]['progress'])
    assert matching.start('game','cross')==result
    busy.state='done'
    finish(jobs,result)
    cross = Matching(net,Jobs()).status('game')['cross']
    assert cross['counts']=={'match':1}
    assert cross['matches'][0]['source_slug']=='another-game'
    assert 'source_code' not in cross['matches'][0]


def test_room_guests_do_not_start_unverifiable_local_jobs(tmp_path):
    net = setup(tmp_path);net.links['game'] = object()
    with pytest.raises(NetworkError,match='host'):
        Matching(net,Jobs()).start('game','deep')
    net.links.clear()


def test_routes_guard_writes_and_do_not_synchronize_saved_projects(tmp_path,monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from decompnet.app.routes.matching import register

    net = setup(tmp_path);handlers = {};seen = []
    class MCP:
        def custom_route(self,path,methods):
            def save(fn):
                handlers[path]=fn
                return fn
            return save
    async def call(fn,*args):
        return fn(*args)
    async def body(request):
        return {'slug':'game','minutes':0}
    def guard(request,write=False):
        seen.append(write)
        return JSONResponse({'error':'guarded'},status_code=403) if request.headers.get('x-deny') else None
    register(SimpleNamespace(net=net,jobs=Jobs(),mcp=MCP(),call=call,body=body,guard=guard,
                             fail=lambda e:JSONResponse({'error':str(e)},status_code=400)))
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Local matching status must not sync.'))
    import asyncio
    read=Request({'type':'http','method':'GET','path':'/api/local-matching','query_string':b'slug=game','headers':[]})
    assert asyncio.run(handlers['/api/local-matching'](read)).status_code==200
    write=Request({'type':'http','method':'POST','path':'/api/permuter/pass','query_string':b'',
                   'headers':[(b'x-deny',b'1')]})
    assert asyncio.run(handlers['/api/permuter/pass'](write)).status_code==403
    assert seen==[False,True]


def test_job_discovery_does_not_wait_for_function_statistics(tmp_path,monkeypatch):
    net=setup(tmp_path);matching=Matching(net,Jobs())
    def forbidden(*args):
        pytest.fail('Control discovery must not scan functions or saved results.')
    monkeypatch.setattr(net.store,'all',forbidden)
    result=matching.control('game')
    assert result['deep']['statistics_pending'] is True
    assert result['deep']['job'] is None
