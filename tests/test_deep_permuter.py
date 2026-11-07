"""A manual search has explicit limits and uses the ordinary permuter judge."""

import json

import pytest

from decompnet.app import deep_permuter
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


class Log:
    def __init__(self):
        self.lines = []
        self.cancelled = False

    def __call__(self, line):
        self.lines.append(line)

    def check_cancel(self):
        if self.cancelled:
            raise RuntimeError("cancelled")


def setup(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("game", "Game", str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,n_insns,status,best_source,best_score,permuted) "
                   "VALUES(?,?,?,'open',?,?,?)", (pid, "f", 10, "int f(void) { return 1; }", 40, 3))
    return net


@pytest.mark.parametrize("minutes,variants", [(0, 10), (121, 10), (True, 10), (1, 0), (1, 1_000_001), (1, 1.5)])
def test_search_rejects_invalid_budgets(tmp_path, minutes, variants):
    net = setup(tmp_path)
    with pytest.raises(NetworkError, match="limit"):
        deep_permuter.validate(net, "game", "f", minutes, variants)


def test_search_does_not_use_the_automatic_turn_allowance(tmp_path):
    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn = net._function("f", "game")
    search = deep_permuter.Search(net, 1, 1, Log())
    assert search._hold(agent, fn)
    assert net._function("f", "game")["permuted"] == 3
    search._let_go(agent, fn)


def test_variant_limit_is_checked_before_another_compile(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn = net._function("f", "game")
    search = deep_permuter.Search(net, 1, 1, Log())
    calls = []
    monkeypatch.setattr(Permuter, "_score", lambda *args: calls.append(args) or 20)
    assert search._score(agent, net.project_row("game"), fn, "candidate", 40, None) == 20
    with pytest.raises(deep_permuter.SearchLimit, match="Variant limit"):
        search._score(agent, net.project_row("game"), fn, "candidate two", 20, None)
    assert len(calls) == 1


def test_search_stops_at_variant_limit_and_keeps_a_verified_improvement(tmp_path, monkeypatch):
    net = setup(tmp_path)
    log = Log()

    def fake_permute(search, agent, proj, fn, state):
        assert search._random_seed(fn) != search._random_seed(fn)
        state.add(variants=1)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=20 WHERE id=?", (fn["id"],))
        search.tested += 1

    monkeypatch.setattr(deep_permuter.Search, "_permute", fake_permute)
    result = deep_permuter.run(net, "game", "f", 1, 1, log)
    assert result == {"function": "f", "reason": "Variant limit reached", "tested": 1,
                      "matched": False, "best_score": 20}
    assert any("verified improvements" in line for line in log.lines)


def test_search_checks_cancellation_before_start(tmp_path):
    net = setup(tmp_path)
    log = Log()
    log.cancelled = True
    with pytest.raises(RuntimeError, match="cancelled"):
        deep_permuter.run(net, "game", "f", 1, 10, log)
