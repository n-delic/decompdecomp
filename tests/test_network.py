import shutil
import sys
from pathlib import Path

import pytest

from decompnet.compiler import SourceRejected, check_source
from decompnet.network import Network, NetworkError

from .handmade import import_project
from .solutions import SOLUTIONS, WRONG_CLAMP

DEMO = Path(__file__).resolve().parent.parent / "projects" / "demo"
# projects/demo is a toy x86-64 game built with the computer's own gcc into ELF objects: Linux only
HOST_GCC = sys.platform != "win32" and shutil.which("gcc") is not None
needs_host_gcc = pytest.mark.skipif(not HOST_GCC, reason="projects/demo needs a Linux gcc (x86-64 ELF)")


@pytest.fixture
def net(tmp_path):
    if not HOST_GCC:
        pytest.skip(needs_host_gcc.kwargs["reason"])
    n = Network(tmp_path / "test.db")
    import_project(n.store, DEMO, log=lambda *_: None)
    return n


@pytest.fixture
def agents(net):
    a = net.authenticate(net.register_agent("alice-claude", "alice"))
    b = net.authenticate(net.register_agent("bob-claude", "bob"))
    return a, b


def status_of(net, name):
    return net.store.one("SELECT status FROM functions WHERE name=?", name)["status"]


def test_check_source_blocks_cheats():
    for bad in ['void f(void){ asm("nop"); }', "#pragma GLOBAL_ASM(\"x.s\")", "#include <stdio.h>",
                'INCLUDE_ASM("asm", f);', '#include "../../etc/passwd"', "void f(void){ __asm__ volatile(\"\"); }"]:
        with pytest.raises(SourceRejected):
            check_source(bad)
    check_source('// this used to be asm\nint f(void) { const char *s = "asm"; return 0; }')


def test_every_solution_matches(net, agents):
    alice, _ = agents
    for name, src in SOLUTIONS.items():
        out = net.compile_and_diff(alice, name, src)
        assert "MATCH" in out, f"{name}:\n{out}"
        assert status_of(net, name) == "matched"


def test_wrong_attempt_gets_scored_diff(net, agents):
    alice, _ = agents
    out = net.compile_and_diff(alice, "clamp_s32", WRONG_CLAMP)
    assert out.startswith("score ")
    assert "TARGET" in out and "YOURS" in out
    assert status_of(net, "clamp_s32") == "open"
    assert net.store.one("SELECT best_score FROM functions WHERE name='clamp_s32'")["best_score"] > 0


def test_compile_error_and_rejection_are_reported(net, agents):
    alice, _ = agents
    assert "COMPILE ERROR" in net.compile_and_diff(alice, "abs_s32", "s32 abs_s32(s32 v) { return v +; }")
    assert "inline assembly" in net.compile_and_diff(alice, "abs_s32", 's32 abs_s32(s32 v) { asm("nop"); return v; }')
    assert "no function named" in net.compile_and_diff(alice, "abs_s32", "s32 other(s32 v) { return v; }")


def test_claims_are_exclusive_and_scheduler_spreads_work(net, agents):
    alice, bob = agents
    a = net.claim(alice)
    b = net.claim(bob)
    fa = a.splitlines()[0].split("/")[-1]
    fb = b.splitlines()[0].split("/")[-1]
    assert fa != fb
    assert fa == "abs_s32"  # easiest first
    with pytest.raises(NetworkError, match="claimed by alice-claude"):
        net.compile_and_diff(bob, fa, SOLUTIONS["abs_s32"])
    with pytest.raises(NetworkError, match="you already hold"):
        net.claim(bob, function=fa)
    net.release(bob, fb, "")
    with pytest.raises(NetworkError, match="claimed by alice-claude"):
        net.claim(bob, function=fa)
    # holding a claim: asking again returns the same function
    assert net.claim(alice).splitlines()[0] == a.splitlines()[0]


def test_release_hands_off_best_attempt_and_notes(net, agents):
    alice, bob = agents
    net.compile_and_diff(alice, "clamp_s32", WRONG_CLAMP)
    net.release(alice, "clamp_s32", "branch order is hi-first in my try; target checks lo first (signed jl)")
    brief = net.claim(bob, function="clamp_s32")
    assert "Best attempt so far" in brief and "(u32)v" in brief
    assert "target checks lo first" in brief
    assert "MATCH" in net.compile_and_diff(bob, "clamp_s32", SOLUTIONS["clamp_s32"])


@needs_host_gcc
def test_expired_claims_are_reclaimable(tmp_path):
    net = Network(tmp_path / "t.db", claim_ttl=-1)  # every claim is born expired
    import_project(net.store, DEMO, log=lambda *_: None)
    alice = net.authenticate(net.register_agent("a"))
    bob = net.authenticate(net.register_agent("b"))
    net.claim(alice, function="abs_s32")
    assert "MATCH" in net.compile_and_diff(bob, "abs_s32", SOLUTIONS["abs_s32"])


def test_context_edits_are_regression_tested(net, agents):
    alice, _ = agents
    net.compile_and_diff(alice, "player_take_damage", SOLUTIONS["player_take_damage"])
    net.compile_and_diff(alice, "entity_update_physics", SOLUTIONS["entity_update_physics"])

    # Changing the Entity layout would silently break matched code -> rejected
    out = net.edit_context(alice, "demo", "/* 0x04 */ s32 x;", "/* 0x04 */ s16 x;", "x is s16?")
    assert "REJECTED" in out and "entity_update_physics" in out
    assert "version 1" in net.get_context("demo")

    # Adding a prototype is safe -> accepted
    out = net.edit_context(alice, "demo", "", "s32 entity_default_hp(s16 type);", "add proto")
    assert "now v2" in out

    # Renaming a field rewrites matched sources too, and they keep matching
    out = net.rename_symbol(alice, "demo", "unk_1C", "hp", "player hp")
    assert "renamed" in out, out
    assert "g_player.hp" in net.store.one("SELECT matched_source FROM functions WHERE name='player_take_damage'")[0]
    assert "s32 hp;" in net.get_context("demo")


def test_scratch_compiles_on_matched_functions_give_no_credit(net, agents):
    alice, bob = agents
    net.compile_and_diff(alice, "abs_s32", SOLUTIONS["abs_s32"])
    out = net.compile_and_diff(bob, "abs_s32", SOLUTIONS["abs_s32"])
    assert "scratch" in out
    row = net.store.one("SELECT a.name FROM functions f JOIN agents a ON a.id=f.matched_agent_id WHERE f.name='abs_s32'")
    assert row["name"] == "alice-claude"


def test_auth(net):
    with pytest.raises(NetworkError):
        net.authenticate("dn_nope")
    with pytest.raises(NetworkError):
        net.register_agent("bad name!")


def test_removing_a_project_removes_everything_in_it(net, agents):
    alice, bob = agents
    net.compile_and_diff(alice, "abs_s32", SOLUTIONS["abs_s32"])
    net.add_note(alice, "demo", "abs is a branchless cmov", "abs_s32")
    net.add_note(bob, "demo", "project-wide tip")
    net.claim(bob)
    assert net.remove_project("demo") == 13
    for table in ("projects", "functions", "attempts", "notes", "events", "context_versions"):
        assert net.store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def test_give_ups_are_recounted_once_without_decomp_wtfs_own_expiries(tmp_path):
    """Before 0.10.4 the autopilot's leftover claims counted as agents giving up (see test_forks): opening the
    database counts each function's give-ups again from releases and agents' own expired claims."""
    import sqlite3

    from decompnet.store import Store

    path = tmp_path / "old.db"
    Store(path)
    db = sqlite3.connect(path)
    db.execute("DELETE FROM settings WHERE key='abandons_recounted'")  # a database from before
    db.execute("INSERT INTO agents(id,name,token_hash,created_at) VALUES(1,'autopilot','x',0),(2,'claude-1','y',0)")
    db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'p','/','{}')")
    db.execute("INSERT INTO functions(project_id,name,abandons) VALUES(1,'f',5),(1,'g',3)")
    for agent, kind, fn in [(1, "expired", "f"), (1, "expired", "f"), (2, "release", "f"), (1, "expired", "g"),
                            (2, "expired", "g"), (2, "release", "g")]:
        db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(1,?,?,?,0)", (agent, kind, fn))
    db.commit()
    db.close()
    store = Store(path)
    assert {r["name"]: r["abandons"] for r in store.all("SELECT name, abandons FROM functions")} == {"f": 1, "g": 2}
    with store.tx() as tx:
        tx.execute("UPDATE functions SET abandons=7 WHERE name='f'")
    assert Store(path).one("SELECT abandons FROM functions WHERE name='f'")["abandons"] == 7  # once only


def test_big_functions_get_more_compiles_before_the_nudge_to_move_on(net, agents):
    alice, _ = agents
    fn = net.store.one("SELECT id FROM functions WHERE name='clamp_s32'")
    with net.store.tx() as db:
        for score in (50, 60, 60, 70, 80, 90, 95):
            db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,source,created_at) VALUES(?,?,?,?,?,?)",
                       (fn["id"], alice.id, score, 1, "x", 0))
    from decompnet.network import stall_budget

    small = stall_budget(net.store.one("SELECT n_insns FROM functions WHERE id=?", fn["id"])["n_insns"])
    assert small < 6
    assert f"⏭ Move on: no better in 6/{small} compiles (move on at {small})" in net.stall_note(fn["id"], alice.id)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET n_insns=300 WHERE id=?", (fn["id"],))
    # a big one: not before 10, and every result still says where it stands
    assert net.stall_note(fn["id"], alice.id).strip() == "Move-on count: no better in 6/10 compiles (move on at 10)."


def test_the_guide_gives_the_compile_budget_compile_and_diff_keeps():
    """The guide said "stuck after ~20 compiles" while compile_and_diff nudged after 4; later the prompt said "3 ... up
    to 10" and agents guessed which number was theirs. The prompt and guides now point at the function's own number,
    which the brief names."""
    from decompnet import network
    from decompnet.app import tools

    said = network.stall_budget_text()
    assert said in " ".join(tools.guide_text("workflow").split())
    assert "up to 10" not in said and "move on at" in said
    for topic in ("workflow", "fork", "similarity"):
        assert "3 compiles in a row" not in " ".join(tools.guide_text(topic).split()), topic
    assert network.stall_budget(1000) == network.STALL_BUDGET_BIGGEST
    fn = {"n_insns": 100}
    assert f"move on after {network.stall_budget(100)} compiles in a row" in network.move_on_brief(fn)
