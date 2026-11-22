"""The hand-off clock: the match-function skill hands a near-match off after 45 minutes without a better score; app
helpers had only the move-on count, in compiles, which a helper thinking for long between compiles never reached."""
import time

from decompnet import network
from decompnet.network import HANDOFF_MINUTES, handoff_due, handoff_line, quiet_minutes

from .test_claims import _game


def test_minutes_since_the_claim_last_improved():
    now = 10_000.0
    assert quiet_minutes([], 4000.0, 20, now) == 100
    rows = [(25, 5000.0), (18, 6000.0), (19, 9000.0)]
    assert quiet_minutes(rows, 4000.0, 20, now) == (now - 6000) / 60  # 18 beat the inherited 20
    assert quiet_minutes([(30, 5000.0)], 4000.0, 20, now) == 100  # never better than the inherited best


def test_due_only_for_a_near_match_past_the_limit():
    near = {'claimed_at': 1, 'best': 20, 'inherited_best': 30, 'quiet_minutes': HANDOFF_MINUTES + 1}
    assert handoff_due(near) and 'release_function(move_on=true)' in handoff_line(near)
    assert not handoff_due(dict(near, quiet_minutes=10)) and '10/45 min' in handoff_line(dict(near, quiet_minutes=10))
    far = dict(near, best=900, inherited_best=900)
    assert not handoff_due(far) and handoff_line(far) == ''
    assert not handoff_due(dict(near, claimed_at=None))


def _attempt(net, agent, fid, score, at):
    with net.store.tx() as db:
        db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at,score_before) "
                   "VALUES(?,?,?,?,?,?,?,?)", (fid, agent.id, score, 1, 1, f"int f(){{return {at};}}", at, 20))


def test_compile_results_show_it_and_release_goes_through_once_it_runs_out(tmp_path):
    net, _ = _game(tmp_path, [("f", 200, "open")])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=20 WHERE name='f'")
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    assert net.claim_row(a, "game", "f")["name"] == "f"
    claimed = time.time() - 60 * 60
    with net.store.tx() as db:
        db.execute("UPDATE claims SET claimed_at=? WHERE function_id=?", (claimed, fid))
    _attempt(net, a, fid, 22, claimed + 60)
    _attempt(net, a, fid, 21, claimed + 120)
    st = net.stall_status(fid, a.id)
    assert st['no_better'] < st['after'] and st['handoff']
    assert '⏱ Hand-off clock' in net.stall_note(fid, a.id)
    said = net.release_function(a, "f", "tried a and b; rows left: two r rows", "game")
    assert said.startswith("Released f")


def test_a_recent_improvement_keeps_the_clock_running(tmp_path):
    net, _ = _game(tmp_path, [("f", 200, "open")])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=20 WHERE name='f'")
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    net.claim_row(a, "game", "f")
    claimed = time.time() - 60 * 60
    with net.store.tx() as db:
        db.execute("UPDATE claims SET claimed_at=? WHERE function_id=?", (claimed, fid))
    _attempt(net, a, fid, 12, time.time() - 60)
    st = net.stall_status(fid, a.id)
    assert not st['handoff'] and 'Hand-off clock: 1/45 min' in net.stall_note(fid, a.id)
    assert not net.release_function(a, "f", "", "game").startswith("Released f")


def test_the_brief_names_the_clock_for_a_near_match():
    assert 'Hand-off clock' in network.move_on_brief({'n_insns': 40, 'best_score': 20})
    assert 'Hand-off clock' not in network.move_on_brief({'n_insns': 40, 'best_score': 900})
