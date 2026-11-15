"""The automatic claim picks its function from a snapshot, outside the project's writer, from covering indexes; the
claim transaction re-checks what it picked, so two workers never hold one function."""

import json
import time

import pytest

from decompnet import network
from decompnet.app.engine import RomNetwork


def _game(tmp_path, functions=4):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for k in range(functions):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,'open','a')",
                       (pid, f"f{k}", 4 * (k + 1), k + 1))
    return net, pid


def _plan(net, sql, *args):
    return [row[3] for row in net.store.conn.execute("EXPLAIN QUERY PLAN " + sql, args)]


def test_claim_history_reads_come_from_covering_indexes_without_sorting(tmp_path):
    """Model routing sorted every claim event of a game for each claim (48,000 of them), and the release cutoff
    walked every agent's 'freed' events, both while holding the project's writer."""
    net, pid = _game(tmp_path)
    route = _plan(net, "SELECT agent_id, COUNT(*) AS n, MAX(created_at) AS last FROM events WHERE project_id=? "
                       "AND kind='claim' GROUP BY agent_id", pid)
    assert any("COVERING INDEX idx_events_project_kind_agent" in step for step in route), route
    assert not any("TEMP B-TREE" in step for step in route), route
    cutoff = _plan(net, "SELECT MAX(created_at) AS t FROM events WHERE agent_id=? AND kind IN ('freed', 'stopped')", 1)
    assert any("COVERING INDEX idx_events_agent_kind_time" in step for step in cutoff), cutoff
    args = {"retry_grants": "[]", "batch_sdk": "[]", "batch_unit": None, "batch_limit": None, "model_tried": "[]",
            "stalled": "[]", "sdk": "[]", "pid": pid, "agent": 1, "kin": "[1]", "cooled": 0, "overall": .5,
            "escalate": 3, "weak": False, "strong": False, "mine": 0, "custom": False, "below": None, "tries": None,
            "machine_pending": "[]"}
    claim = [row[3] for row in net.store.conn.execute("EXPLAIN QUERY PLAN " + network.claim_query("easiest"), args)]
    on_events = [step for step in claim if "events" in step and "idx_events_project_time" not in step]
    assert on_events and all("COVERING INDEX idx_events_project_kind_agent" in step for step in on_events), claim


def test_a_function_taken_after_the_pick_is_not_claimed_twice(tmp_path, monkeypatch):
    net, _ = _game(tmp_path)
    first, _ = net.local_agent("first")
    second, _ = net.local_agent("second")
    pick = network.Network._claim_pick
    raced = []

    def racing(read, query, args, route):
        fn = pick(read, query, args, route)
        if not raced:  # another worker claims the same function between the snapshot and the transaction
            raced.append(None)
            raced[0] = net.claim_row(second, "game", None, "easiest")["name"]
        return fn

    monkeypatch.setattr(net, "_claim_pick", racing)
    taken = net.claim_row(first, "game", None, "easiest")["name"]
    assert raced == ["f0"] and taken == "f1"
    holders = net.store.all("SELECT f.name, c.agent_id FROM claims c JOIN functions f ON f.id=c.function_id")
    assert sorted((r["name"], r["agent_id"]) for r in holders) == [("f0", second.id), ("f1", first.id)]


def test_a_lease_that_ran_out_frees_its_function_for_the_next_claim(tmp_path):
    """The snapshot still shows the expired lease; the transaction expires it and picks again."""
    net, _ = _game(tmp_path, functions=1)
    first, _ = net.local_agent("first")
    second, _ = net.local_agent("second")
    assert net.claim_row(first, "game", None, "easiest")["name"] == "f0"
    with net.store.tx() as db:
        db.execute("UPDATE claims SET expires_at=?", (time.time() - 1,))
    assert net.claim_row(second, "game", None, "easiest")["name"] == "f0"
    assert [r["agent_id"] for r in net.store.all("SELECT agent_id FROM claims")] == [second.id]


@pytest.mark.parametrize("project_databases", [False, True])
def test_a_function_id_finds_its_project_database(tmp_path, project_databases):
    net = RomNetwork(tmp_path / "app.db", tmp_path, project_databases=project_databases)
    ids = {}
    for slug in ("one", "two"):
        with net.project_registration(slug=slug, name=slug, description="", arch="mips", big_endian=False,
                                      root=str(tmp_path / slug), config={"kind": "rom"}) as (db, pid):
            ids[slug] = db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) "
                                   "VALUES(?,'f',4,1,'open','a')", (pid,)).lastrowid
    for slug, fid in ids.items():
        assert net.function_store(fid) is net.project_store(slug)
        assert net.function_store(fid) is net.project_store(slug)  # again, from what the first lookup learned
    assert net.function_store(max(ids.values()) + 1000) is None
