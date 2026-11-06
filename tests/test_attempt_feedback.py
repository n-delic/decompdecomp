"""Homemade regressions for compiler drift, cross-agent stalls and unsupported dependency claims."""

import json
import sqlite3

import pytest

from decompnet import attempts, scoring
from decompnet.arch import mips
from decompnet.elfobj import FunctionObj
from decompnet.network import Network, NetworkError
from decompnet.store import Store

from .test_claims import _game


def retained(net, agent, compiler=None):
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    measured = attempts.provenance(compiler, "int f(){return 1;}", header="typedef int s32;", context_version=3) if compiler else None
    net._record(fn, agent, 50, "int f(){return 1;}", 3, provenance=measured)
    return net.store.one("SELECT * FROM functions WHERE name='f'")


def test_best_source_keeps_its_measured_compiler_when_a_later_route_is_worse(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    gcc = {"toolchain": "ee-gcc-2.9-991111", "flags": ["-O2", "-G8"]}
    fn = retained(net, a, gcc)
    other = {"toolchain": "mwcps2-2.3.3", "flags": ["-O3"]}
    net._record(fn, a, 800, fn["best_source"], 3,
                provenance=attempts.provenance(other, fn["best_source"], header="typedef int s32;", context_version=3))
    text = "\n".join(attempts.brief(net, fn, other, "typedef int s32;"))
    assert "ee-gcc-2.9-991111 -O2 -G8" in text and "COMPILER CHANGED" in text
    assert "shared header changed" not in text
    text = "\n".join(attempts.brief(net, fn, gcc, "typedef short s32;"))
    assert "shared header changed" in text and "COMPILER CHANGED" not in text
    row = net.store.one("SELECT provenance FROM attempts WHERE score=50")
    assert json.loads(row["provenance"])["source_sha256"] == attempts.digest(fn["best_source"])


def test_legacy_and_reported_scores_do_not_invent_compiler_provenance(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    fn = retained(net, a)
    text = "\n".join(attempts.brief(net, fn, {"toolchain": "today", "flags": []}))
    assert "not recorded" in text and "Measured with today" not in text


def test_repeated_regressions_are_visible_across_agents_and_restarts(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    fn = retained(net, a)
    b, _ = net.local_agent("b")
    for agent in [a, b, a, b]:
        net._record(fn, agent, 800, "int f(){return 2;}", 3)
    # Fill the bounded handoff window with regressions, as a long-running project does.
    for _ in range(8):
        net._record(fn, b, 800, "int f(){return 2;}", 3)
    restarted = Network(tmp_path / "app.db")
    text = "\n".join(attempts.brief(restarted, fn))
    assert "12 attempts by 2 agents" in text and "one new hypothesis" in text


def test_a_dependency_needs_evidence_and_is_not_presented_as_verified(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 20, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    with pytest.raises(NetworkError, match="needs_reason"):
        net.release_function(a, "f", "g is unmatched", "game", needs="g")
    assert not net.store.one("SELECT * FROM waits")
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    g = net.store.one("SELECT * FROM functions WHERE name='g'")
    net.wait_for(a, fn, g, "unknown argument width at the call")
    text = "\n".join(net.dependency_brief(fn))
    assert "not verified" in text and "unknown argument width" in text
    assert "unmatched callee is not itself a blocker" in text
    # Releasing after observing a cached attempt withdraws the hypothesis without wasting another compile.
    key = net.compile_key(fn, "same inputs")
    net.remember_compile(key, a, fn, "score 50", 50)
    net.repeat_of(key, a, fn)
    net.release_function(a, "f", "the declaration is known now", "game")
    assert not net.store.one("SELECT * FROM waits")


def test_target_changes_invalidate_repeat_results(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    fn = dict(net.store.one("SELECT * FROM functions WHERE name='f'"))
    key = net.compile_key(fn, "header", "source")
    for field, value in [("target", b"new bytes"), ("vram", 4096), ("relocs", "[]"), ("name", "renamed")]:
        changed = {**fn, field: value}
        assert net.compile_key(changed, "header", "source") != key


def test_old_database_gains_nullable_provenance_without_fabricating_history(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, function_id INTEGER, agent_id INTEGER, score INTEGER, "
               "compiled INTEGER, context_version INTEGER, source TEXT, created_at REAL)")
    db.execute("INSERT INTO attempts VALUES(1,1,1,50,1,3,'old source',0)")
    db.commit()
    db.close()
    store = Store(path)
    assert store.one("SELECT provenance FROM attempts WHERE id=1")["provenance"] is None


@pytest.mark.parametrize("big_endian", [False, True])
def test_ee_quadword_and_mmi_words_are_not_mips_dsp_instructions(big_endian):
    # Homemade instruction sequence: por v0,zero,zero; sq v0,0x10(a0); lq v1,0x10(a0); jr ra; nop.
    words = [0x700014A9, 0x7C820010, 0x78830010, 0x03E00008, 0]
    code = b"".join(w.to_bytes(4, "big" if big_endian else "little") for w in words)
    target = FunctionObj("f", "mips", big_endian, code, [], cpu=mips.R5900)
    assert [i.mnemonic for i in scoring.disassemble(target)][:3] == ["por", "sq", "lq"]
    # The candidate inherits the target CPU; an incorrect constant still cannot match.
    candidate = FunctionObj("f", "mips", big_endian, code, [])
    assert scoring.diff(target, candidate).bytes_match
    candidate.code = b"".join(w.to_bytes(4, "big" if big_endian else "little") for w in [words[0], words[1] + 16, *words[2:]])
    d = scoring.diff(target, candidate)
    assert not d.bytes_match and d.score > 0
    assert all(".byte" not in t + c and "subu.qb" not in t + c for _, t, c in d.rows)


def test_repeat_handoff_restores_diff_and_does_not_force_a_dummy_compile_to_release(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    b, _ = net.local_agent("b")
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    rows = [("i", "addiu v0,zero,1", "addiu v0,zero,2")]
    net.keep_diff(a.id, fn["id"], rows)
    key = net.compile_key(fn, "source")
    net.remember_compile(key, a, fn, "score 10", 10)
    net.claim_row(b, "game", "f")
    assert net.repeat_of(key, b, fn)["repeat"]
    assert "addiu v0,zero,1" in net.last_diff(b, "f", "game")
    assert net.release_function(b, "f", "already measured; try a different compiler next", "game").startswith("Released")
    assert net.store.one("SELECT count(*) n FROM attempts")["n"] == 0
    net.claim_row(b, "game", "f")
    assert net.release_function(b, "f", "new claim without a probe", "game").startswith("Not released yet")


def test_alternate_probe_is_retained_without_claiming_a_match_or_spending_budget(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    fn = retained(net, a)
    measured = attempts.provenance({"toolchain": "alternate", "flags": ["-O2"]}, "int f(){return 2;}",
                                    header="header", context_version=4)
    attempts.record_probe(net, fn, a.id, "int f(){return 2;}", measured, 0, True)
    attempts.record_probe(net, fn, a.id, "int f(){return 2;}", measured, 0, True)
    assert net.store.one("SELECT count(*) n FROM compiler_probes")["n"] == 1
    assert net.store.one("SELECT count(*) n FROM attempts")["n"] == 1
    assert net.store.one("SELECT status FROM functions")["status"] == "open"
    text = "\n".join(attempts.brief(net, fn, header="header"))
    assert "alternate -O2" in text and "not an accepted match" in text and "return 2" in text
    assert "Historical inputs" not in text
    assert "Historical inputs" in "\n".join(attempts.brief(net, fn, header="changed"))


def test_dropping_a_function_cannot_leave_a_probe_for_a_reused_id(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    fn = net.store.one("SELECT * FROM functions WHERE name='f'")
    measured = attempts.provenance({"toolchain": "alternate", "flags": []}, "source", header="header")
    attempts.record_probe(net, fn, a.id, "source", measured, 20, False)
    with net.store.tx() as db:
        db.execute("DELETE FROM functions WHERE id=?", (fn["id"],))
    assert not net.store.one("SELECT * FROM compiler_probes")
