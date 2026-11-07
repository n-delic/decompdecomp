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
        self.snapshot = None

    def progress(self, values):
        self.snapshot = values

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
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(?,1,'',0)", (pid,))
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
    assert search.log.snapshot["starting_score"] == 40
    assert search.log.snapshot["best_score"] == 20
    assert search.log.snapshot["tested"] == 1
    assert search.log.snapshot["history"] == [{"tested": 0, "score": 40}, {"tested": 1, "score": 20}]


def test_search_stops_at_variant_limit_and_keeps_a_verified_improvement(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    log = Log()

    def fake_permute(search, agent, proj, fn, state):
        assert search._random_seed(fn) != search._random_seed(fn)
        state.add(variants=1)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_score=20 WHERE id=?", (fn["id"],))
        search.tested += 1

    monkeypatch.setattr(deep_permuter.Search, "_permute", fake_permute)
    monkeypatch.setattr(Permuter, "_score", lambda *args: 40)
    result = deep_permuter.run(net, "game", "f", 1, 2, log)
    assert result == {"function": "f", "reason": "Variant limit reached", "tested": 2,
                      "matched": False, "best_score": 20}
    assert any("verified improvements" in line for line in log.lines)


def test_search_checks_cancellation_before_start(tmp_path):
    net = setup(tmp_path)
    log = Log()
    log.cancelled = True
    with pytest.raises(RuntimeError, match="cancelled"):
        deep_permuter.run(net, "game", "f", 1, 10, log)


def test_duplicate_misses_and_compile_failures_do_not_spend_more_compiles(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn, proj = net._function("f", "game"), net.project_row("game")
    search = deep_permuter.Search(net, 1, 10, Log())
    calls = []
    monkeypatch.setattr(Permuter, "_score", lambda *args: calls.append(args) or (None if args[4] == "broken" else 50))
    for source in ("candidate", "candidate", "broken", "broken"):
        search._score(agent, proj, fn, source, 40, None)
    assert len(calls) == search.tested == 2
    assert search.skipped == 2
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='new header'")
    search._score(agent, proj, fn, "candidate", 40, None)
    assert search.tested == 3  # changed inputs invalidate the earlier result


def test_baseline_rebases_a_stale_score_and_blocks_changed_inputs(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn, proj = net._function("f", "game"), net.project_row("game")
    search = deep_permuter.Search(net, 1, 10, Log())
    monkeypatch.setattr(Permuter, "_score", lambda *args: 80)
    assert search.baseline(agent, proj, fn, None) == 80
    assert net._function("f", "game")["best_score"] == search.starting_score == 80
    assert search.tested == 1
    with net.store.tx() as db:
        db.execute("UPDATE context_versions SET header='changed'")
    with pytest.raises(deep_permuter.SearchLimit, match="header or target changed"):
        search._score(agent, proj, fn, "candidate", 80, None)
    assert search.tested == 1


def test_broken_baseline_stops_before_mutations_and_releases_claim(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    monkeypatch.setattr(Permuter, "_score", lambda *args: None)
    with pytest.raises(NetworkError, match="no longer compiles"):
        deep_permuter.run(net, "game", "f", 1, 10, Log())
    assert not net.store.one("SELECT 1 FROM claims")


def test_baseline_never_replaces_a_concurrent_winner(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn, proj = net._function("f", "game"), net.project_row("game")

    def concurrent_change(*args):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source='new winner',best_score=20")
        return 80

    monkeypatch.setattr(Permuter, "_score", concurrent_change)
    with pytest.raises(NetworkError, match="saved candidate changed"):
        deep_permuter.Search(net, 1, 10, Log()).baseline(agent, proj, fn, None)
    current = net._function("f", "game")
    assert current["best_source"] == "new winner" and current["best_score"] == 20


@pytest.mark.parametrize("fork", [None, "imported"])
def test_potential_winners_are_always_rejudged(tmp_path, monkeypatch, fork):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    agent, _ = net.local_agent("permuter")
    fn, proj = net._function("f", "game"), net.project_row("game")
    search = deep_permuter.Search(net, 1, 10, Log())
    calls = []
    monkeypatch.setattr(Permuter, "_score", lambda *args: calls.append(args) or 20)
    for _ in range(2):
        assert search._score(agent, proj, fn, "candidate", 40, fork) == 20
    assert len(calls) == search.tested == 2
    assert search.skipped == 0


def test_local_rewrites_precede_random_search_and_use_the_same_budget(tmp_path, monkeypatch):
    from decompnet.app.permute import Permuter

    net = setup(tmp_path)
    source = "int f(int a) { return a + (a << 5); }"
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=?", (source,))
    judged = []
    monkeypatch.setattr(Permuter, "_score", lambda *args: judged.append(args[4]) or 40)
    monkeypatch.setattr(deep_permuter.Search, "_permute", lambda *args: pytest.fail("budget already spent"))
    result = deep_permuter.run(net, "game", "f", 1, 2, Log())
    assert judged == [source, "int f(int a) { return (a << 5) + a; }"]
    assert result["tested"] == 2
