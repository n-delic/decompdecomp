"""A selective draft refresh respects room leases and host confirmation in both project modes."""
from types import SimpleNamespace

import pytest

from decompnet import seeds, toolchains
from decompnet.app import draft_regeneration, draft_reports
from decompnet.network import NetworkError

from .test_overlay_drafts import network  # noqa: F401
from .test_rom import built  # noqa: F401


@pytest.mark.parametrize('baseline', ['scored', 'compiled_unscorable', 'compiled_attempt', 'uncompiled_seed'])
@pytest.mark.parametrize('include_all', [False, True])
def test_refresh_scope_uses_saved_compilation_evidence(network, monkeypatch, baseline, include_all):  # noqa: F811
    net, proj, fn = network
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    calls = []
    monkeypatch.setattr(draft_regeneration, 'generate', lambda *args: calls.append('generated') or 'fresh draft')
    monkeypatch.setattr(draft_regeneration, 'trial', lambda *args: ('near', {}))
    with net.store.tx() as db:
        if baseline == 'scored':
            db.execute("UPDATE functions SET best_source='saved C',best_score=12 WHERE id=?", (fn['id'],))
        else:
            db.execute("UPDATE functions SET seed='uncompiled starting C' WHERE id=?", (fn['id'],))
    if baseline == 'compiled_attempt':
        with net.store.tx() as db:
            db.execute('INSERT INTO attempts(function_id,compiled,source) VALUES(?,1,?)',(fn['id'],'compiled but not scored'))
    if baseline == 'compiled_unscorable':
        draft_reports.save(net, fn['id'], {'compiled':True,'compiled_source':'retained C','scored':False})
    before = net._draft_plan(fn, proj)[0]
    result = draft_regeneration.run(net, proj['slug'], lambda *args: None, include_all=include_all)
    selected = include_all or baseline == 'uncompiled_seed'
    assert result['total'] == len(calls) == int(selected)
    assert (net._draft_plan(fn, proj)[0] != before) == selected
    assert net.cfg(net.project_row(proj['slug'])).get('draft_generation', 0) == 0
    current = net._function(fn['name'], proj['slug'])
    assert current['best_source'] == ('saved C' if baseline == 'scored' else None)


def _room_trial(network_fixture, monkeypatch, *, fork=False, outcome='match', confirm=True, claim_error=False, judge_error=False):
    net, proj, fn = network_fixture
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    events = []
    def claim(agent, name, strategy):
        events.append(('claim', agent.name, name))
        if claim_error:raise NetworkError('The room host is unavailable.')
        return name
    def send(agent, name, source):
        events.append(('attempt', source))
        return {'matched':confirm, 'reason':'Host settings differ.'}
    def call(route, agent, **data):
        events.append((route, data))
    net.links[proj['slug']] = SimpleNamespace(claims=[],claim=claim,attempt=send,call=call)
    monkeypatch.setattr(net, 'fork_slug', lambda slug: slug if fork else None)
    monkeypatch.setattr(draft_regeneration, 'generate', lambda *args: events.append(('generate',)) or 'fresh draft')
    def judge(_net, agent, project, fid, source, is_fork):
        events.append(('judge', agent.name))
        if judge_error:raise NetworkError('Compiler test failed unexpectedly.')
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_source=?,best_score=?,matched_source=? WHERE id=?',
                       ('compiled draft', 0 if outcome == 'match' else 12, 'compiled draft' if outcome == 'match' else None, fid))
        report = {'compiled':True,'matched':outcome == 'match'}
        if fork:
            # The project's ordinary attempt path already sends its candidate to the room host.
            report['host_confirmed'] = confirm
        draft_reports.save(net, fid, report)
        return outcome, report
    monkeypatch.setattr(draft_regeneration, 'trial', judge)
    result = draft_regeneration.run(net, proj['slug'], lambda *args: None)
    return result, events, draft_reports.read(net, fn['id'])


@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('confirm', [False, True])
def test_room_refresh_counts_only_host_confirmed_matches(network, monkeypatch, fork, confirm):  # noqa: F811
    result, events, report = _room_trial(network, monkeypatch, fork=fork, confirm=confirm)
    assert result['compiled'] == 1 and result['matched'] == int(confirm)
    assert [e[0] for e in events][:3] == ['generate', 'claim', 'judge']
    assert report['host_confirmed'] == confirm
    assert sum(e[0] == 'attempt' for e in events) == int(not fork)
    assert any(e[0] == '/collab/release' for e in events) == (not confirm)
    if not confirm and not fork:
        assert 'awaiting host confirmation' in draft_reports.summary(report)


def test_room_refresh_shares_better_nonmatching_c_and_releases_lease(network, monkeypatch):  # noqa: F811
    result, events, _ = _room_trial(network, monkeypatch, outcome='near')
    assert result['compiled'] == 1 and result['matched'] == 0
    shared = [e for e in events if e[0] == '/collab/best']
    assert shared[0][1]['source'] == 'compiled draft' and shared[0][1]['score'] == 12
    assert events[-1][0] == '/collab/release'


def test_room_refusal_keeps_generated_c_and_never_starts_judging(network, monkeypatch):  # noqa: F811
    result, events, _ = _room_trial(network, monkeypatch, claim_error=True)
    assert result['failed'] == 1
    assert [e[0] for e in events] == ['generate', 'claim']


@pytest.mark.skipif(not seeds.available() or toolchains.installed('gcc-2.7.2-kmc') is None,
                    reason='needs pinned m2c and the period compiler')
def test_room_member_refresh_runs_the_local_compiler_and_host_exact_judge(built, tmp_path, monkeypatch):  # noqa: F811
    from starlette.testclient import TestClient

    from decompnet.app import collab
    from decompnet.app.engine import RomNetwork

    from .test_app_engine import DAMAGE

    host_home, guest_home = tmp_path/'host', tmp_path/'guest'
    host_home.mkdir(); guest_home.mkdir()
    host = RomNetwork(host_home/'db',host_home)
    slug = host.import_rom(built,confirmed=True,log=lambda *_:None)
    host.rooms[slug] = collab.Room(host,slug)
    code = host.rooms[slug].open_invite()
    client = TestClient(collab.host_app(host,host.rooms),base_url='https://abc.trycloudflare.com')
    guest = RomNetwork(guest_home/'db',guest_home)
    gslug = collab.join_room(guest,f'https://abc.trycloudflare.com/join/{code}#room={slug}',
                            'draft-member',built,log=lambda *_:None,client=client,start=False)
    name = 'func_80000808'
    with guest.store.tx() as db:
        db.execute("UPDATE functions SET status='not_c' WHERE project_id=? AND name<>?",(guest.project_row(gslug)['id'],name))
    monkeypatch.setattr(seeds,'generate_from_asm',lambda *args,**kwargs:DAMAGE)
    result = draft_regeneration.run(guest,gslug,lambda *_:None)
    assert result['total'] == result['compiled'] == result['matched'] == 1, result
    assert guest._function(name,gslug)['status'] == host._function(name,slug)['status'] == 'matched'
    assert not host.store.one('SELECT 1 FROM claims')
    assert not guest.store.one('SELECT 1 FROM claims')
    report = draft_reports.read(guest,guest._function(name,gslug)['id'])
    assert report['host_confirmed']
    assert host.store.one('SELECT count(*) AS n FROM attempts')['n'] > 0
    assert guest.store.one('SELECT count(*) AS n FROM attempts')['n'] > 0


def test_failed_room_trial_releases_its_host_lease(network, monkeypatch):  # noqa: F811
    result, events, _ = _room_trial(network,monkeypatch,judge_error=True)
    assert result['failed'] == 1
    assert events[-1][0] == '/collab/release'


def test_concurrent_room_trials_use_distinct_reusable_workers(network, monkeypatch):  # noqa: F811
    net, proj, fn = network
    with net.store.tx() as db:
        db.execute('INSERT INTO functions(project_id,name,vram,n_insns) VALUES(?,?,?,?)',
                   (proj['id'],'second',fn['vram']+32,6))
    result, events, _ = _room_trial(network,monkeypatch,outcome='near')
    assert result['completed'] == 2 and result['compiled'] == 2
    claimed = [e[1] for e in events if e[0] == 'claim']
    assert len(set(claimed)) == 2
    assert {e[1] for e in events if e[0] == 'judge'} == set(claimed)


@pytest.mark.parametrize('verdict',[True,False,'offline'])
def test_project_attempt_exposes_host_confirmation_separately_from_local_match(network, monkeypatch, verdict):  # noqa: F811
    net, proj, fn = network
    agent, _ = net.local_agent('drafts-test')
    def send(*args):
        if verdict == 'offline':raise NetworkError('Host is offline.')
        return {'matched':verdict,'score':0 if verdict else 12}
    net.links[proj['slug']] = SimpleNamespace(attempt=send)
    monkeypatch.setattr(net, 'check_attempt_limit', lambda *args: None)
    monkeypatch.setattr(net.forks, '_fn', lambda *args: fn)
    monkeypatch.setattr(net.forks, '_attempt', lambda *args,**kwargs: {'score':0,'matched':True,'text':'Local match.'})
    monkeypatch.setattr(net.forks, '_follow', lambda *args: None)
    result = net.forks._attempt_and_share(agent,proj['slug'],fn['name'],'candidate C')
    assert result['matched'] is True
    assert result['host_confirmed'] == (verdict is True)


@pytest.mark.parametrize('value',[True,-1,65,'8',None])
def test_draft_thread_count_rejects_invalid_limits(value):
    with pytest.raises(ValueError):draft_regeneration.thread_count(value)


def test_regeneration_has_independent_parallel_workers(network,monkeypatch):  # noqa: F811
    import threading

    from decompnet.app import workers
    net,proj,fn=network
    with net.store.tx() as db:
        for i in range(3):
            db.execute('INSERT INTO functions(project_id,name,vram,n_insns) VALUES(?,?,?,?)',
                       (proj['id'],'parallel_'+str(i),fn['vram']+32*(i+1),6))
    monkeypatch.setattr(net,'can_draft',lambda proj:True)
    monkeypatch.setattr(workers,'WORKERS',1)
    monkeypatch.setattr(workers,'pool',lambda:pytest.fail('Explicit regeneration must not use the shared pool'))
    monkeypatch.setattr(net,'_autopilot_one',lambda *a:pytest.fail('No autopilot variant search'))
    barrier=threading.Barrier(4)
    generated=[]
    def generate(*args):
        from decompnet import compiler
        assert not getattr(compiler._SCHEDULING, "background", False)
        generated.append(threading.current_thread().name)
        barrier.wait(timeout=3)
        return 'raw draft'
    monkeypatch.setattr(draft_regeneration,'generate',generate)
    monkeypatch.setattr(draft_regeneration,'trial',lambda *args:('near',{'compiled':True,'trials':1}))
    result=draft_regeneration.run(net,proj['slug'],lambda *a:None,threads=4)
    assert result['completed']==result['compiled']==4
    assert len(set(generated))==4 and all(name.startswith('draft-refresh') for name in generated)


def test_failed_raw_draft_gets_exactly_one_compile_without_variant_search(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    agent,_=net.local_agent('raw-draft-test')
    compiled=[]
    monkeypatch.setattr(net,'_compile',lambda *args:compiled.append(args[2]) or SimpleNamespace(ok=False,log='raw draft failed'))
    monkeypatch.setattr(net,'_autopilot_one',lambda *a:pytest.fail('No variant search'))
    outcome,report=draft_regeneration.trial(net,agent,proj,fn['id'],'raw source',False)
    assert compiled==['raw source'] and outcome=='fail'
    assert report['trials']==1 and not report['compiled']
    assert not net.store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id'])


def test_regeneration_bypasses_cached_seed_and_generates_once(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    generated=[]
    monkeypatch.setattr(seeds,'generate_from_asm',lambda *a,**kw:generated.append(kw) or 'int fresh(void){return 1;}')
    monkeypatch.setattr(net,'_seed_for',lambda *a:pytest.fail('Regeneration must not reuse cached generation'))
    text=draft_regeneration.generate(net,proj,fn,False)
    assert len(generated)==1 and 'int fresh' in text
    assert net._function(fn['name'],proj['slug'])['seed']==text


def test_imported_regeneration_submits_only_the_raw_draft_once(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    agent,_=net.local_agent('raw-fork-draft')
    calls=[]
    def attempt(*args,**kwargs):
        calls.append((args,kwargs))
        return {'score':12,'matched':False}
    monkeypatch.setattr(net.forks,'attempt',attempt)
    monkeypatch.setattr(net.forks,'autopilot_one',lambda *a:pytest.fail('No imported variant search'))
    outcome,report=draft_regeneration.trial(net,agent,proj,fn['id'],'raw imported C',True)
    assert len(calls)==1 and calls[0][0][-1]=='raw imported C' and calls[0][1]=={'keep':False}
    assert outcome=='near' and report['trials']==1 and report['compiled']
