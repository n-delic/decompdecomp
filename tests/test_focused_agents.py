"""Focus is a scheduler constraint, with a deadline shared by every provider and mode."""
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import agents, focused_agents, tools
from decompnet.network import Network, NetworkError
from tests.test_agents import URL, _game, quiet  # noqa: F401


def setup(tmp_path):
    net=_game(tmp_path)
    agent,_=net.local_agent('focused-test')
    fn=net._function('f0','game')
    s=SimpleNamespace(slug='game',stop=threading.Event(),options={
        'focus_id':fn['id'],'focus_until':time.time()+60,'focus_function':fn['name']})
    focused_agents.bind(net,s,agent)
    return net,agent,s


def test_claims_are_pinned_and_batch_cannot_add_other_functions(tmp_path):
    net,agent,s=setup(tmp_path)
    with net.store.tx() as db:db.execute("INSERT INTO functions(project_id,name,status) VALUES(1,'f1','open')")
    assert net.focus_target(agent,'game')=='f0'
    with pytest.raises(NetworkError,match='cannot claim other functions'):
        net.claim_row(agent,'game','f1')
    fn=net.claim_row(agent,'game')
    assert fn['name']=='f0'
    assert Network.claim_row(net,agent,'game',batch_from={}) is None
    assert len(net.store.all('SELECT * FROM claims WHERE agent_id=?',agent.id))==1
    with net.store.tx() as db:db.execute("UPDATE functions SET name='new_name' WHERE id=?",(s.options['focus_id'],))
    assert net.focus_target(agent,'game')=='new_name'


def test_focused_tools_never_fall_back_when_target_is_busy(tmp_path,monkeypatch):
    net,agent,s=setup(tmp_path)
    other,_=net.local_agent('other-test')
    net.claim_row(other,'game','f0')
    monkeypatch.setattr(net,'_brief',lambda *a,**kw:'claimed')
    with pytest.raises(NetworkError,match='claimed by'):
        tools._claim(net,agent,'game','','',count=6)
    assert not net.store.one('SELECT 1 FROM claims WHERE agent_id=?',agent.id)
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',s.options['focus_id'])['agent_id']==other.id


def test_deadline_and_user_stop_reject_late_work(tmp_path):
    net,agent,s=setup(tmp_path)
    net._agent_focus[agent.id]['until']=time.time()-1
    with pytest.raises(NetworkError,match='ended'):net.ensure_agent_active(agent)
    net._agent_focus[agent.id]['until']=time.time()+60;s.stop.set()
    with pytest.raises(NetworkError,match='ended'):net.claim_row(agent,'game')


def test_completion_only_checks_the_chosen_function(tmp_path):
    net,agent,s=setup(tmp_path);manager=SimpleNamespace(net=net)
    assert focused_agents.completion(manager,s) is None
    assert 'time limit' in focused_agents.completion(manager,s,now=s.options['focus_until'])
    with net.store.tx() as db:db.execute("UPDATE functions SET status='matched' WHERE id=?",(s.options['focus_id'],))
    assert 'complete' in focused_agents.completion(manager,s)


def test_validation_and_persisted_deadline_survive_restore(tmp_path,quiet):  # noqa: F811
    net=_game(tmp_path);manager=agents.Manager(net,tmp_path,URL)
    for minutes in (0,241,True,1.5,'30'):
        with pytest.raises(ValueError,match='minutes'):
            manager.start('codex','game',options={'focus_function':'f0','focus_minutes':minutes})
    for tries in (0, 10001, True, 1.5, '30'):
        with pytest.raises(ValueError, match='tries'):
            manager.start('codex','game',options={'focus_function':'f0','focus_max_tries':tries})
    with pytest.raises(ValueError,match='one matching agent'):
        manager.start('codex','game',2,{'focus_function':'f0','focus_minutes':30})
    sid=manager.start('codex','game',options={'focus_function':'f0','focus_minutes':30})[0]
    deadline=manager.sessions[sid].options['focus_until']
    assert 1790<deadline-time.time()<=1800
    restored=agents.Manager(net,tmp_path,URL)
    assert restored.sessions[sid].options['focus_until']==deadline
    restored._launch(restored.sessions[sid])
    assert restored.sessions[sid].options['focus_until']==deadline
    snapshot=restored.sessions[sid].snapshot()
    assert snapshot['focus_function']=='f0'
    assert snapshot['focus_id']==net._function('f0','game')['id']
    assert snapshot['focus_minutes']==30
    assert snapshot['focus_max_tries']==300


def test_expired_restored_run_stops_without_model_call(tmp_path):
    net,agent,s=setup(tmp_path);s.options['focus_until']=time.time()-1;s.id='focus'
    reasons=[];stops=[]
    manager=SimpleNamespace(net=net,_finish=lambda session,why:reasons.append(why),
                            stop=lambda sid,by:stops.append(sid),
                            _run_work=lambda _:pytest.fail('must not start model'))
    focused_agents.run(manager,s)
    assert reasons and stops==['focus']


def test_deadline_stops_an_inflight_worker(tmp_path):
    net,agent,s=setup(tmp_path);s.options['focus_until']=time.time()+.1;s.id='focus'
    reasons=[]
    manager=SimpleNamespace(net=net,_finish=lambda session,why:reasons.append(why),
                            stop=lambda sid,by:s.stop.set(),_run_work=lambda session:session.stop.wait(3))
    started=time.monotonic();focused_agents.run(manager,s)
    assert time.monotonic()-started<2
    assert s.stop.is_set() and 'time limit' in reasons[0]


def test_imported_project_and_room_claim_entry_is_pinned(tmp_path,monkeypatch):
    net,agent,s=setup(tmp_path);seen=[]
    monkeypatch.setattr(net,'fork_slug',lambda project:'game')
    net.forks.claim=lambda who,slug,function,strategy:seen.append(function) or 'focused claim'
    assert net.claim_many(agent,'game',6)=='focused claim'
    assert seen==['f0']
    with pytest.raises(NetworkError,match='another project'):net.focus_target(agent,'other')


def test_focused_agent_ui():
    node=shutil.which('node')
    if not node:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_focused_agents.js'))],check=True,timeout=30)


def test_verified_match_ends_worker_without_waiting_for_deadline(tmp_path):
    net,agent,s=setup(tmp_path);s.id='focus';reasons=[]
    def work(session):
        with net.store.tx() as db:db.execute("UPDATE functions SET status='matched' WHERE id=?",(s.options['focus_id'],))
        assert session.stop.wait(2)
    manager=SimpleNamespace(net=net,_finish=lambda session,why:reasons.append(why),
                            stop=lambda sid,by:s.stop.set(),_run_work=work)
    focused_agents.run(manager,s)
    assert 'complete' in reasons[0]


def test_focused_creation_records_worker_without_saved_file_sync(tmp_path,quiet,monkeypatch):  # noqa: F811
    net=_game(tmp_path)
    manager=agents.Manager(net,tmp_path,URL)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Creation waited for saved-file verification'))
    monkeypatch.setattr(manager,'_prepare',lambda *_:pytest.fail('Focused creation prepared the whole project'))
    sid=manager.start('codex','game',options={'focus_function':'f0','focus_minutes':15})[0]
    assert manager.sessions[sid].options['focus_id']==net.store.one("SELECT id FROM functions WHERE name='f0'")['id']
    assert not net.progress.viewing


def test_focused_worker_skips_matching_waits_and_inline_sync(tmp_path,quiet,monkeypatch):  # noqa: F811
    net=_game(tmp_path)
    manager=agents.Manager(net,tmp_path,URL)
    sid=manager.start('codex','game',options={'focus_function':'f0','focus_minutes':15})[0]
    class Waiting:
        def is_set(self):return False
        def wait(self,*args):pytest.fail('Focused worker waited for unrelated matching preparation')
    manager._prep['game']=Waiting()
    monkeypatch.setattr(net.twins,'jobs',{'game':SimpleNamespace(ordered=Waiting())})
    def sync(*args,**kwargs):
        pytest.fail('Focused startup restored saved files inline')
    monkeypatch.setattr(net.progress,'sync',sync)
    class Ready(Exception):pass
    def run(*args, **kwargs):
        assert net.progress.viewing
        raise Ready()
    monkeypatch.setattr(manager, '_left', run)
    with pytest.raises(Ready):manager._run_work(manager.sessions[sid])
    assert not net.progress.viewing



def test_focused_route_does_not_start_whole_project_work(tmp_path,quiet,monkeypatch):  # noqa: F811
    from starlette.testclient import TestClient

    from decompnet.app.server import build
    from tests.test_app_server import PORT, SECRET, WINDOW
    net=_game(tmp_path)
    mcp,manager,security,_=build(net,tmp_path,PORT,SECRET)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Route waited for saved files'))
    monkeypatch.setattr(net,'warm_briefs_soon',lambda *_:pytest.fail('Focused route warmed the whole project'))
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    response=client.post('/api/agents/start',json={'slug':'game','kind':'codex','count':1,
        'options':{'focus_function':'f0','focus_max_tries':12,'focus_minutes':15}},headers={'X-Decomp':'1'})
    assert response.status_code==200,response.text
    sid, = response.json()['started']
    assert manager.sessions[sid].snapshot()['focus_max_tries'] == 12


@pytest.mark.parametrize('fork', [False, True])
@pytest.mark.parametrize('final_match', [False, True])
def test_try_budget_counts_candidate_errors_and_allows_final_match(tmp_path, monkeypatch, fork, final_match):
    net, agent, s = setup(tmp_path)
    s.agent_name = agent.name
    fn = net._function('f0', 'game')
    # An earlier run by the same worker retains its history without spending this run's budget.
    net._record(fn, agent, 50, 'old work', 1)
    s.options.update(focus_max_tries=2, focus_since=time.time())
    focused_agents.bind(net, s, agent)
    net.claim_row(agent, 'game')
    builds = []

    def judge(agent, *args, **kwargs):
        source = args[2] if fork else args[1]
        builds.append(source)
        score = None if source == 'syntax error' else 0 if final_match else 5
        net._record(fn, agent, score, source, 1, matched=score == 0)
        return {'text': 'Synthetic verdict', 'score': score, 'matched': score == 0}

    monkeypatch.setattr(net, 'on_match', lambda *args: None)
    if fork:
        monkeypatch.setattr(net.forks, '_fn', lambda *args: net._function('f0', 'game'))
        monkeypatch.setattr(net.forks, '_attempt_and_share', judge)
        attempt = lambda source: net.forks.attempt(agent, 'game', 'f0', source)
    else:
        monkeypatch.setattr(net, '_attempt_unlimited', judge)
        attempt = lambda source: net.attempt(agent, 'f0', source, 'game')
    attempt('syntax error')
    assert net.attempt_limit(agent, fn) == (2, 1)
    assert focused_agents.completion(SimpleNamespace(net=net), s) is None
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,status) VALUES(1,'other','open')")
    with pytest.raises(NetworkError, match='Only the assigned function'):
        with net.attempt_turn(agent, net._function('other', 'game')):
            pytest.fail('A focused agent tried to judge a second function.')

    # Parallel tools compete for the last try through the ordinary candidate lock.
    from concurrent.futures import ThreadPoolExecutor
    def submit(source):
        try:
            return attempt(source)
        except NetworkError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(submit, ['improved work', 'another improvement']))
    assert len(builds) == 2 and sum(isinstance(outcome, str) for outcome in outcomes) == 1
    assert net.attempt_limit(agent, fn) == (2, 2)
    reason = focused_agents.completion(SimpleNamespace(net=net), s)
    assert ('Verified match' if final_match else 'try limit reached') in reason
    assert net._function('f0', 'game')['best_source'] == builds[-1]
    assert s.options['focus_tries_used'] == 2


def test_focused_app_assignment_is_in_startup_and_stale_lease_tools_are_rejected(tmp_path, monkeypatch):
    from decompnet.agents.api import api_tool_schemas
    from decompnet.app import tool_policy
    from decompnet.app.tool_reader import _hidden

    net, agent, s = setup(tmp_path)
    s.startup_context = None
    s.agent_name = agent.name
    net._pending_tool_handshakes = {(agent.id, 'game')}
    monkeypatch.setattr(tools, '_project_info', lambda *a, **kw: 'Synthetic facts')
    monkeypatch.setattr(net, '_brief', lambda *a, **kw: 'CLAIMED f0')
    manager = agents.Manager(net, tmp_path, URL)
    manager.prepare_session(s, agent)
    assert 'CLAIMED f0' in s.startup_context
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?', s.options['focus_id'])['agent_id'] == agent.id
    assert (agent.id, 'game') in net._pending_tool_handshakes
    tools.call(net, agent, 'game', 'project_info', {'detail': False})
    names = tools.project_tools(net, 'game', agent)
    schema = {tool['function']['name'] for tool in api_tool_schemas(names=names)}
    assert tool_policy.connection_required(net, agent) <= schema
    assert _hidden(net, {str(agent.id): 'game'}) == {str(agent.id): ['claim_function']}
    for name in ('claim_function', 'release_function', 'release_and_continue'):
        assert name not in schema
        response = tools.call(net, agent, 'game', name, {'function': 'f0', 'notes': 'give up'})
        assert 'app manages this focused assignment' in response
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?', s.options['focus_id'])['agent_id'] == agent.id


def test_restored_budget_starts_with_previous_usage_and_manual_restart_resets_it(tmp_path, quiet):  # noqa: F811
    net = _game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)
    sid, = manager.start('codex', 'game', options={'focus_function': 'f0', 'focus_max_tries': 1,
                                                  'focus_minutes': None})
    s = manager.sessions[sid]
    agent, _ = net.local_agent(s.agent_name)
    focused_agents.bind(net, s, agent)
    fn = net.claim_row(agent, 'game')
    net._record(fn, agent, 5, 'saved candidate', 1)
    manager.stop()
    restored = agents.Manager(net, tmp_path, URL)
    saved = restored.sessions[sid]
    assert 'focus_until' not in saved.options
    assert saved.options['focus_since'] == s.options['focus_since']
    assert '1/1' in focused_agents.completion(restored, saved)
    restored.restart(sid)
    assert focused_agents.completion(restored, saved) is None
    assert saved.snapshot()['focus_tries_used'] == 0
    assert net._function('f0', 'game')['best_source'] == 'saved candidate'


def test_a_focused_run_without_a_choice_starts_on_the_connectors_strongest(tmp_path, quiet, monkeypatch):  # noqa: F811
    from decompnet import agents as connectors
    net = _game(tmp_path); manager = agents.Manager(net, tmp_path, URL)
    codex = connectors.get('codex')
    monkeypatch.setattr(type(codex), 'config_model', lambda self: None)
    sid = manager.start('codex', 'game', options={'focus_function': 'f0', 'focus_minutes': 30})[0]
    assert manager.sessions[sid].options.get('effort') == codex.focus_effort
    # the user's own choice wins
    sid = manager.start('codex', 'game', options={'focus_function': 'f0', 'focus_minutes': 30, 'effort': 'low'})[0]
    assert manager.sessions[sid].options['effort'] == 'low'
    # a normal (unfocused) helper is unchanged
    sid = manager.start('codex', 'game', options={})[0]
    assert not manager.sessions[sid].options.get('effort')
