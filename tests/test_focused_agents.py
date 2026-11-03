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
    with pytest.raises(ValueError,match='one matching agent'):
        manager.start('codex','game',2,{'focus_function':'f0','focus_minutes':30})
    sid=manager.start('codex','game',options={'focus_function':'f0','focus_minutes':30})[0]
    deadline=manager.sessions[sid].options['focus_until']
    assert 1790<deadline-time.time()<=1800
    restored=agents.Manager(net,tmp_path,URL)
    assert restored.sessions[sid].options['focus_until']==deadline
    restored._launch(restored.sessions[sid])
    assert restored.sessions[sid].options['focus_until']==deadline
    assert restored.sessions[sid].snapshot()['focus_function']=='f0'


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
