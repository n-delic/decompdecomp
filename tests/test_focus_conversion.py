"""An opted-in helper that claims a stuck near-match becomes a focused run on it, and goes back afterwards; one
not opted in never does (focused_agents.convert)."""
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet.app import agents, claim_state, focused_agents
from tests.test_agents import URL, _game, quiet  # noqa: F401


def _stuck(net, attempts=focused_agents.STUCK_ATTEMPTS, best=12):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET attempts=?, best_score=?, status='open' WHERE name='f0'", (attempts, best))


def _helper(net, opted):
    agent, _ = net.local_agent('helper-1')
    saved = []
    s = SimpleNamespace(slug='game', stop=threading.Event(), agent_name='helper-1', options={'focus_near_matches': opted},
                        push=lambda *a: None)
    manager = SimpleNamespace(net=net, _save=saved.append)
    focused_agents.bind_conversion(manager, s, agent)
    return agent, s, manager, saved


def test_thresholds():
    fn = lambda attempts, best: {'status': 'open', 'attempts': attempts, 'best_score': best}
    assert focused_agents.stuck(fn(50, 50)) and focused_agents.stuck(fn(300, 12))
    assert not focused_agents.stuck(fn(49, 12)) and not focused_agents.stuck(fn(300, 51))
    assert not focused_agents.stuck(fn(300, 0)) and not focused_agents.stuck(fn(300, None))


def test_opted_in_converts_and_reverts(tmp_path):
    net = _game(tmp_path); _stuck(net)
    agent, s, manager, saved = _helper(net, True)
    net.claim_row(agent, 'game', 'f0')
    assert claim_state.claimed_name(net, agent, 'game') == 'f0'
    note = focused_agents.convert(net, agent, 'game', 'f0')
    assert 'Focused on f0' in note and s.options['focus_function'] == 'f0' and s.options['focus_converted']
    assert focused_agents.scope(net, agent)['id'] == s.options['focus_id'] and saved
    s.options['focus_until'] = time.time() - 1  # the run ends: back to normal rotation, not stopped
    assert focused_agents.ready(manager, s, agent) is True
    assert 'focus_id' not in s.options and 'focus_converted' not in s.options and not focused_agents.scope(net, agent)


def test_opted_out_or_not_stuck_never_converts(tmp_path):
    net = _game(tmp_path); _stuck(net)
    agent, s, _, _ = _helper(net, False)
    assert focused_agents.convert(net, agent, 'game', 'f0') == '' and 'focus_id' not in s.options
    _stuck(net, attempts=3)
    agent, s, _, _ = _helper(net, True)
    assert focused_agents.convert(net, agent, 'game', 'f0') == '' and 'focus_id' not in s.options


def test_the_choice_is_validated_and_persists(tmp_path, quiet):  # noqa: F811
    net = _game(tmp_path); manager = agents.Manager(net, tmp_path, URL)
    with pytest.raises(ValueError, match='near-matches'):
        manager.start('codex', 'game', options={'focus_near_matches': 'yes'})
    sid = manager.start('codex', 'game', options={'focus_near_matches': True})[0]
    other = manager.start('codex', 'game', options={})[0]
    restored = agents.Manager(net, tmp_path, URL)
    assert restored.sessions[sid].snapshot()['focus_near_matches'] is True
    assert restored.sessions[other].snapshot()['focus_near_matches'] is False
