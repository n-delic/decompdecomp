"""The permuter (app/permute.py): decomp-permuter's variants in batches, judged by our own judge, on workers that
yield the CPU; and the simple rewrites (permuter.py) when the tool isn't there. A stand-in engine (FakeNet) and a
stand-in generator do without any compiler; the end-to-end test on the demo ROM is in test_speed.py."""

import json
import os
import sys
import threading
import time

import pytest

from decompnet import toolchains
from decompnet.app import permute
from decompnet.app.permute import MAX_TURNS, Permuter, State, generate, weight_family
from decompnet.network import Agent
from decompnet.store import IN_MEMORY, Store

SOURCE = "s32 f(s32 a) {\n    s32 b = a + 1;\n    return b * 2;\n}\n"
HEADER = "/* the game's types */\n#define TWO 2\ntypedef int s32;\n"


class FakeNet:
    """What the permuter uses of the engine: the store (real, in memory), one project with one near miss, and its
    header. The judge is the test's own (ScriptedPermuter._score)."""

    links: dict = {}

    def __init__(self):
        self.store = Store(IN_MEMORY)
        with self.store.tx() as db:
            db.execute("INSERT INTO projects(id, slug, name, arch, root, config) VALUES(1, 'game', 'Game', 'mips', '.', ?)",
                       (json.dumps({"compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": "-O2"}}),))
            db.execute("INSERT INTO agents(id, name, token_hash) VALUES(1, 'permuter', 'x')")
            db.execute("INSERT INTO functions(id, project_id, name, n_insns, best_score, best_source, vram) "
                       "VALUES(1, 1, 'f', 10, 40, ?, 2147483648)", (SOURCE,))

    def cfg(self, proj):
        return json.loads(proj["config"])

    def fork_slug(self, slug):
        return None

    def project_row(self, slug):
        return self.store.one("SELECT * FROM projects WHERE slug=?", slug)

    def function(self):
        return self.store.one("SELECT * FROM functions WHERE id=1")

    def _context(self, project_id, unit):
        return {"header": HEADER, "version": 1}

    def _expire_claims(self, db):
        pass


class ScriptedPermuter(Permuter):
    """Scores from a table (C -> score; None: it doesn't compile; anything else is far off)."""

    def __init__(self, net, scores):
        super().__init__(net)
        self.scores, self.judged = scores, []

    def _score(self, agent, proj, fn, source, best, fork):
        self.judged.append(source)
        return self.scores.get(source, 1000)


AGENT = Agent(1, "permuter", None)


def batches(monkeypatch, *outputs):
    """The generator's answers, one per run, and the jobs it was given."""
    jobs, answers = [], list(outputs)
    monkeypatch.setattr(permute, "available", lambda: True)
    monkeypatch.setattr(permute, "generate", lambda job, argv=None: jobs.append(job) or (answers.pop(0) if answers else []))
    return jobs


def test_variants_come_in_batches_from_the_first_and_the_best_c_in_turn(monkeypatch):
    """Batches start from the C the turn began with and from the best so far, taking turns; a variant that doesn't
    compile is skipped; a match ends the turn, and the function is let go for agents."""
    net = FakeNet()
    p = ScriptedPermuter(net, {"broken": None, "closer": 10, "same": 0})
    jobs = batches(monkeypatch, ["broken", "far", "closer"], ["far too"], ["same", "never judged"])
    st = State()
    p._permute(AGENT, net.project_row("game"), net.function(), st)
    assert [j["source"] for j in jobs] == [SOURCE, "closer", SOURCE]
    assert jobs[0]["header"] == HEADER and jobs[0]["function"] == "f" and jobs[0]["family"] == "gcc"
    assert p.judged == ["broken", "far", "closer", "far too", "same"]
    assert (st.variants, st.improved, st.matched, st.tried, st.current) == (5, 2, 1, 1, "")
    assert net.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0


def test_c_decomp_permuter_cant_read_falls_back_to_the_simple_rewrites(monkeypatch):
    net = FakeNet()
    p = ScriptedPermuter(net, {})
    jobs = batches(monkeypatch, None)
    monkeypatch.setattr(permute, "SECONDS", 0.3)
    p._permute(AGENT, net.project_row("game"), net.function(), State())
    assert len(jobs) == 1 and p.judged[0] != SOURCE and all("s32 f(s32 a)" in c for c in p.judged)


def test_c_no_simple_rewrite_applies_to_ends_the_turn(monkeypatch):
    """Nothing in lerp's `return a + (b - a) * t;` is one of the simple rewrites: the turn ends, where it used to
    try for a whole minute on a core."""
    net = FakeNet()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE id=1", ("f32 f(f32 a, f32 b, f32 t) {\n    return a + (b - a) * t;\n}\n",))
    monkeypatch.setattr(permute, "available", lambda: False)
    p = ScriptedPermuter(net, {})
    started = time.monotonic()
    p._permute(AGENT, net.project_row("game"), net.function(), State())
    assert time.monotonic() - started < permute.SECONDS / 10 and p.judged == []


def test_a_function_gets_its_turns_even_across_restarts(monkeypatch):
    """The turns a function had are in the database: a restarted app doesn't permute it again from the start."""
    net = FakeNet()
    batches(monkeypatch)  # nothing new to write: each turn ends at once
    proj = net.project_row("game")
    for _ in range(MAX_TURNS):
        assert [r["name"] for r in Permuter(net).candidates(proj)] == ["f"]
        ScriptedPermuter(net, {})._permute(AGENT, proj, net.function(), State())
    assert net.function()["permuted"] == MAX_TURNS and Permuter(net).candidates(proj) == []


def test_a_project_is_permuted_by_the_shared_workers_until_stopped(monkeypatch):
    net = FakeNet()
    net.local_agent = lambda name: (AGENT, "")
    batches(monkeypatch, *[["same"]] * MAX_TURNS)
    p = ScriptedPermuter(net, {"same": 0})
    st = p.start("game")
    deadline = time.monotonic() + 10
    while st.matched < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    p.stop("game")
    while st.state == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert st.matched >= 1 and st.state == "stopped" and p.status("game")["matched"] == st.matched
    assert "_lock" not in p.status("game") and "stop" not in p.status("game")


def test_workers_yield_the_cpu_to_the_app():
    """A worker's CPU priority is lower than the app's, and the compilers it runs inherit it (Linux: per thread)."""
    def priority():
        return os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

    app = priority()
    if app >= 19:
        pytest.skip("this test already runs at the lowest priority")
    p = Permuter(FakeNet())
    assert p._workers().submit(priority).result() == min(19, app + permute.NICE) and priority() == app
    assert 1 <= permute.WORKERS <= permute.MAX_WORKERS


def test_compilers_pick_decomp_permuters_weights():
    kmc = {"compiler": {"toolchain": "gcc-2.7.2-kmc"}}
    gta = {"compiler": {"toolchain": "ee-gcc-2.95.3"},
           "compiler_ranges": [{"start": 0x200000, "end": 0x300000, "toolchain": "mwcps2-3.0.1", "flags": "-O3"}]}
    assert weight_family(kmc, 0x80000400, False) == "gcc"
    assert weight_family({"compiler": {"toolchain": "ido-7.1"}}, None, False) == "ido"
    assert [weight_family(gta, vram, False) for vram in (0x100000, 0x200010)] == ["gcc", "mwcc"]
    assert weight_family({"compiler": {"cmd": ["cc"]}}, None, False) == "base"
    assert weight_family({"platform": "gc"}, None, True) == "mwcc" and weight_family({"platform": "win32"}, None, True) == "base"


FAKE_GENERATOR = """
import json, sys, time
job = json.load(sys.stdin)
if job["source"] == "hang":
    time.sleep(30)
if job["source"] == "unreadable":
    print(json.dumps({"error": "Syntax error"}))
    sys.exit(1)
if job["source"] == "crash":
    sys.exit(3)
print("a stray line")
print(3)
for n in range(job["count"]):
    print(json.dumps({"variant": f"{job['source']} {job['family']} {job['seed'] + n}"}))
"""


def test_the_generator_answers_in_json_lines(tmp_path, monkeypatch):
    """The generator's side of the protocol, with a stand-in: variants line by line; and None (the simple rewrites
    take over) for C it can't read, a crash, or a run that hangs."""
    script = tmp_path / "gen.py"
    script.write_text(FAKE_GENERATOR)
    argv = [sys.executable, str(script)]
    job = {"header": "", "source": "C", "function": "f", "family": "gcc", "seed": 7, "count": 3}
    assert generate(job, argv) == ["C gcc 7", "C gcc 8", "C gcc 9"]
    assert generate({**job, "source": "unreadable"}, argv) is None
    assert generate({**job, "source": "crash"}, argv) is None
    monkeypatch.setattr(permute, "GENERATOR_SECONDS", 0.5)
    assert generate({**job, "source": "hang"}, argv) is None


needs_tool = pytest.mark.skipif(toolchains.installed(permute.TOOL) is None, reason="needs decomp-permuter")


@needs_tool
def test_decomp_permuter_writes_variants_of_the_function_alone():
    job = {"header": HEADER, "source": SOURCE, "function": "f", "family": "gcc", "seed": 1, "count": 20}
    variants = generate(job)
    assert len(variants) == len(set(variants)) == 20
    assert all(" f(" in v and "typedef" not in v and "the game's types" not in v for v in variants)
    assert generate({**job, "source": "s32 f(s32 a) { return M2C_FIELD(a, s32 *, 4); }"}) is None  # a macro call
