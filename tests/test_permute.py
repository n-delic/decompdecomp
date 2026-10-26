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
from decompnet.app import permute, workers
from decompnet.app.permute import MAX_TURNS, Permuter, State, weight_family
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
    """The generator's answers, one per batch, and the jobs it was given."""
    jobs, answers = [], list(outputs)

    class ScriptedGenerator:
        def __init__(self, argv=None):
            pass

        def batch(self, job):
            jobs.append(job)
            return answers.pop(0) if answers else []

        def close(self):
            pass

    monkeypatch.setattr(permute, "available", lambda: True)
    monkeypatch.setattr(permute, "Generator", ScriptedGenerator)
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


def test_the_generator_gets_the_header_as_c(monkeypatch):
    """GTA San Andreas's header keeps its classes in an `#ifdef __cplusplus` block, which pycparser can't read: the
    generator gets the header a C compiler sees."""
    net = FakeNet()
    net._context = lambda project_id, unit: {"header": HEADER + "#ifdef __cplusplus\nclass C { public: s32 x; };\n"
                                                         "#endif\n", "version": 1}
    jobs = batches(monkeypatch, [])
    ScriptedPermuter(net, {})._permute(AGENT, net.project_row("game"), net.function(), State())
    assert jobs[0]["header"].rstrip() == HEADER.rstrip() and "class" not in jobs[0]["header"]


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
        if os.name == "nt":
            import ctypes

            kernel = ctypes.windll.kernel32
            kernel.GetCurrentThread.restype = ctypes.c_void_p
            kernel.GetThreadPriority.argtypes = (ctypes.c_void_p,)
            return kernel.GetThreadPriority(kernel.GetCurrentThread())
        return os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

    if sys.platform == "darwin":
        import ctypes

        system = ctypes.CDLL(None)
        system.qos_class_self.restype = ctypes.c_uint
        app_qos = system.qos_class_self()
        app_nice = os.getpriority(os.PRIO_PROCESS, 0)
        assert workers.pool().submit(system.qos_class_self).result() == 0x11
        assert system.qos_class_self() == app_qos and os.getpriority(os.PRIO_PROCESS, 0) == app_nice
        return
    app = priority()
    if os.name == "nt":
        assert workers.pool().submit(priority).result() == -1 and priority() == app
        return
    if app >= 19:
        pytest.skip("this test already runs at the lowest priority")
    assert workers.pool().submit(priority).result() == min(19, app + workers.NICE) and priority() == app
    assert 1 <= workers.WORKERS <= workers.MAX_WORKERS


def test_drafts_and_the_permuter_share_one_pool_of_workers(monkeypatch):
    """Two pools of four made eight background threads beside the agents' compiles: both submit to the one pool."""
    from decompnet.app import drafts

    used = []
    monkeypatch.setattr(workers, "pool", lambda: used.append("pool") or workers.ThreadPoolExecutor(1))
    net = FakeNet()
    net.local_agent = lambda name: (AGENT, "")
    p = Permuter(net)
    p.candidates = lambda proj: [net.function()]
    p._turn = lambda *args: p.stop("game")
    p._loop("game", p.jobs.setdefault("game", State()))
    d = drafts.Drafter(net)
    net.draft_mark, net.can_draft, net.stale_drafts = (lambda proj: 1), (lambda proj: True), (lambda proj: [1])
    d._one = lambda *args: d.stop("game")
    d._loop("game", d.jobs.setdefault("game", drafts.State()))
    assert used == ["pool", "pool"]
    assert workers.WORKERS <= workers.MAX_WORKERS == 4


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
for number, line in enumerate(sys.stdin, 1):
    job = json.loads(line)
    if job["source"] == "hang":
        time.sleep(30)
    if job["source"] == "crash":
        sys.exit(3)
    if job["source"] == "unreadable":
        print(json.dumps({"error": "Syntax error"}))
    else:
        print("a stray line")
        print(3)
        for n in range(job["count"]):
            print(json.dumps({"variant": f"job {number}: {job['source']} {job['family']} {job['seed'] + n}"}))
    print(json.dumps({"done": True}), flush=True)
"""


def test_the_generator_answers_in_json_lines(tmp_path, monkeypatch):
    """The generator's side of the protocol, with a stand-in: one process for the turn, jobs in and variants out
    line by line; and None (the simple rewrites take over) for C it can't read, a crash, or a batch that hangs."""
    script = tmp_path / "gen.py"
    script.write_text(FAKE_GENERATOR)
    argv = [sys.executable, str(script)]
    job = {"header": "", "source": "C", "function": "f", "family": "gcc", "seed": 7, "count": 3}
    gen = permute.Generator(argv)
    assert gen.batch(job) == ["job 1: C gcc 7", "job 1: C gcc 8", "job 1: C gcc 9"]
    assert gen.batch({**job, "seed": 1, "count": 1}) == ["job 2: C gcc 1"]  # the same process: parsed once
    assert gen.batch({**job, "source": "unreadable"}) is None
    assert gen.batch({**job, "source": "crash"}) is None and gen.proc.poll() == 3
    monkeypatch.setattr(permute, "GENERATOR_SECONDS", 0.5)
    gen = permute.Generator(argv)
    assert gen.batch({**job, "source": "hang"}) is None and gen.proc.poll() is not None


needs_tool = pytest.mark.skipif(toolchains.installed(permute.TOOL) is None, reason="needs decomp-permuter")


@needs_tool
def test_decomp_permuter_writes_variants_of_the_function_alone():
    job = {"header": HEADER, "source": SOURCE, "function": "f", "family": "gcc", "seed": 1, "count": 20}
    gen = permute.Generator()
    try:
        variants = gen.batch(job)
        assert len(variants) == len(set(variants)) == 20
        assert all(" f(" in v and "typedef" not in v and "the game's types" not in v for v in variants)
        assert gen.batch({**job, "source": "s32 f(s32 a) { return M2C_FIELD(a, s32 *, 4); }"}) is None  # a macro call
        assert gen.batch({**job, "seed": 2}) != variants  # it answers on after C it couldn't read
    finally:
        gen.close()


@needs_tool
def test_autopilots_drafts_are_permuted_with_their_macros_kept():
    """A draft autopilot judged starts with m2c's macros (#ifndef M2C_DEFINES): pycparser reads no preprocessor
    lines, so the near misses autopilot left (a PSP demo's every one) never got decomp-permuter's variants. The
    lines are read around, and each variant gets them back for the compiler."""
    from decompnet import seeds

    draft = f"{seeds.M2C_MACROS}\nvoid g(s32);\n\nvoid f(s32 a) {{\n    g(M2C_ERROR(/* unknown instruction */));\n    g(a);\n}}\n"
    gen = permute.Generator()
    try:
        header = HEADER + "typedef signed char s8;\ntypedef short s16;\n"  # (the macros' types use them)
        variants = gen.batch({"header": header, "source": draft, "function": "f", "family": "gcc", "seed": 1, "count": 5})
    finally:
        gen.close()
    assert variants and all(v.startswith("#ifndef M2C_DEFINES\n#define M2C_DEFINES\n") and " f(" in v for v in variants)


def test_a_source_the_project_refuses_is_one_line_in_the_log(tmp_path, caplog, monkeypatch):
    """A best attempt with a helper the source file hasn't: every variant the permuter sent was refused, each with a
    traceback in the user's log. The refusal is an answer, not a crash."""
    from decompnet.app.engine import RomNetwork
    from decompnet.network import NetworkError

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES('g','G','','arm',0,?,'{}')",
                   (str(tmp_path),))
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(1,'f',8,2,'open','a')")
    p = permute.Permuter.__new__(permute.Permuter)
    p.net = net
    monkeypatch.setattr(p, "_permute", lambda *a: (_ for _ in ()).throw(NetworkError("send the function definition\nmore")),
                        raising=False)
    with caplog.at_level("INFO", logger="decompdecomp"):
        p._turn(None, None, {"name": "f", "id": 1}, None)
    assert caplog.records[-1].getMessage() == "permuter: f: send the function definition (left to agents)"
    assert caplog.records[-1].exc_info is None
    # the same best attempt is refused every time: its other turns go too
    assert net.store.one("SELECT permuted FROM functions WHERE id=1")["permuted"] == permute.MAX_TURNS
