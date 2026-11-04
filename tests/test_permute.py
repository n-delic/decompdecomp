"""The permuter (app/permute.py): decomp-permuter's variants in batches, judged by our own judge, on workers that
yield the CPU; and the simple rewrites (permuter.py) when the tool isn't there. A stand-in engine (FakeNet) and a
stand-in generator do without any compiler; the end-to-end test on the demo ROM is in test_speed.py."""

import json
import os
import re
import sys
import threading
import time

import pytest

from decompnet import seeds, toolchains
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
    """Scores from a table (C -> score; None: it doesn't compile; an exception: the project refuses it; anything else
    is far off)."""

    def __init__(self, net, scores):
        super().__init__(net)
        self.scores, self.judged = scores, []

    def _score(self, agent, proj, fn, source, best, fork):
        self.judged.append(source)
        score = self.scores.get(source, 1000)
        if isinstance(score, Exception):
            raise score
        return score


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
    assert jobs[0]["header"] == "typedef int s32;" and jobs[0]["function"] == "f" and jobs[0]["family"] == "gcc"
    assert p.judged == ["broken", "far", "closer", "far too", "same"]
    assert (st.variants, st.improved, st.matched, st.tried, st.current) == (5, 2, 1, 1, "")
    assert net.store.one("SELECT COUNT(*) AS n FROM claims")["n"] == 0


def test_a_projects_turn_judges_its_variants_in_one_run_of_tries(monkeypatch):
    """Each variant of a project's function built its unit twice, the second time to put the file back: a turn is
    one run of tries (ForkEngine.trial_run), the unit built once at its end, before the function is let go."""
    from contextlib import contextmanager

    events = []

    class Forks:
        @contextmanager
        def trial_run(self, proj, unit):
            events.append(("run", unit))
            yield
            events.append(("end", unit))

        def permuter_header(self, fork, name):
            return HEADER

        def permuter_name(self, fork, name, source):
            return name

    class ForkNet(FakeNet):
        forks = Forks()

        def fork_slug(self, slug):
            return slug

    class Recording(ScriptedPermuter):
        def _score(self, agent, proj, fn, source, best, fork):
            events.append(("judge", source))
            return super()._score(agent, proj, fn, source, best, fork)

        def _let_go(self, agent, fn):
            events.append(("let go", fn["name"]))
            super()._let_go(agent, fn)

    net = ForkNet()
    batches(monkeypatch, ["far", "same"])
    Recording(net, {"same": 0})._permute(AGENT, net.project_row("game"), net.function(), State())
    assert events == [("run", ""), ("judge", "far"), ("judge", "same"), ("end", ""), ("let go", "f")]


def test_decomp_permuter_looks_a_function_up_by_the_name_its_c_has(monkeypatch):
    """A PC or Xbox game's function is its mangled symbol, which no C spells: every turn ended with "Function
    ?Update@@YAXH@Z not found in base.c". The generator gets the name the definition has (ForkEngine._names)."""
    import json as _json

    from decompnet.app.forkengine import ForkEngine

    class Engine:
        _meta, _names, _find = staticmethod(ForkEngine._meta), staticmethod(ForkEngine._names), ForkEngine._find

        def _fn(self, slug, name):
            return {"name": name, "meta": _json.dumps({"symbol": name, "demangled": "void __cdecl Update(int)"})}

    source = "void Update(int n)\n{\n    g = n;\n}\n"
    assert ForkEngine.permuter_name(Engine(), "game", "?Update@@YAXH@Z", source) == "Update"
    assert ForkEngine.permuter_name(Engine(), "game", "?Update@@YAXH@Z", "int x;\n") == "?Update@@YAXH@Z"
    # defined on a class the source calls something else: the definition's own spelling, which the generator finds
    other = "// a note (with parentheses)\nvoid C_vt_003AB500 :: Update(int n)\n{\n    g = n;\n}\n"
    assert ForkEngine.permuter_name(Engine(), "game", "?Update@@YAXH@Z", other) == "C_vt_003AB500::Update"


def test_the_generator_gets_the_header_as_c(monkeypatch):
    """GTA San Andreas's header keeps its classes in an `#ifdef __cplusplus` block, which pycparser can't read: the
    generator gets the header a C compiler sees."""
    net = FakeNet()
    net._context = lambda project_id, unit: {"header": HEADER + "#ifdef __cplusplus\nclass C { public: s32 x; };\n"
                                                         "#endif\n", "version": 1}
    jobs = batches(monkeypatch, [])
    ScriptedPermuter(net, {})._permute(AGENT, net.project_row("game"), net.function(), State())
    assert "typedef int s32;" in jobs[0]["header"] and "class" not in jobs[0]["header"]


def test_the_generator_gets_the_part_of_the_header_the_function_uses():
    """An x86 game's headers are thousands of lines of C++ with MSVC's words, and one line pycparser couldn't read
    anywhere in them stopped decomp-permuter on every function. It gets the declarations the function needs, read
    after MSVC's words go: `struct __declspec(align(16)) Vec4` used to be a struct named `__declspec`."""
    header = ("typedef unsigned __int64 Id;\n"
              "struct __declspec(align(16)) Vec4 { float x, y, z, w; };\n"
              "struct Unused { int a; };\nclass Widget { public: void Draw(); int n; };\n"
              "void Scale(Vec4 *v, float s);\n")
    source = "void f(Vec4 *v, Id id) { Scale(v, 2.0f); }\n"
    part = permute.permuter_header(header, source)
    assert "struct Vec4 {" in part and "DD_uint64 Id" in part and "Scale" in part
    assert "Unused" not in part and "Widget" not in part and "__declspec" not in part


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
    assert [weight_family({"compiler": {"toolchain": t}}, None, False) for t in ("ps3dev-gcc", "ps3sdk-270")] == ["gcc"] * 2
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


CXX_HEADER = """#define QUALIFIER volatile
#define METHODS public: void Slot0(int arg); void Release();
struct Vec2 { float x, y; Vec2() {} Vec2(float ax, float ay) : x(ax), y(ay) {}
    Vec2 operator+(const Vec2 &o) const { Vec2 r; r.x = x + o.x; return r; } };
class Base { public: virtual ~Base(); int id; };
class Widget : public Base {
    METHODS
public:
    static int s_count;
    Vec2 pos;
    Widget *next;
    QUALIFIER int mode;
    HANDLE file;
    int (*callback)(Widget *w);
    bool Visible() const { return mode != 0; }
};
inline Vec2 Add(const Vec2 &a, const Vec2 &b) { return a + b; }
extern "C" int __stdcall Open(const char *, size_t);
static Vec2 s_origin = Vec2(1.0f, 2.0f);
Vec2 g_corner(0.5f, 0.5f);
template <class T> T Max(T a, T b) { return a > b ? a : b; }
"""


def test_a_cxx_header_is_read_as_the_c_of_its_types():
    """What pycparser can't read of a header written for C++ is left out or rewritten, so a C function among the
    classes gets decomp-permuter's variants: classes as structs of their data, methods and base classes gone, every
    tag a type by its own name, references as pointers, macros put in, the types of system headers declared."""
    from decompnet import permuter_gen as g

    header = g.cxx_as_c(g.msvc_as_c(g.plain_c(g.expand_macros(g.plain_c(CXX_HEADER)), directives=False)))
    assert "class" not in header and "Slot0" not in header and "Visible" not in header and "operator" not in header
    assert "volatile int mode;" in header and "int (*callback)(Widget *w);" in header and "s_count" not in header
    assert "Vec2 Add(const Vec2 *a, const Vec2 *b);" in header and "s_origin;" in header and "g_corner" not in header
    assert "Max" not in header and "Open(const char *, size_t);" in header
    assert "typedef int PDWORD;" in g.type_names(header, "void f(int *p) { Use((PDWORD) &p); }")
    assert "n;" not in g.type_names(header, "int f(int n) { return (n) + 1; }")
    names = g.type_names(header, "bool f(Widget *w) { return w->mode; }")
    for line in ("typedef struct Widget Widget;", "typedef struct Vec2 Vec2;", "typedef int HANDLE;",
                 "typedef int size_t;", "typedef _Bool bool;"):
        assert line in names


def test_msvcs_words_go_back_into_each_variant():
    """`__int64` has a stand-in type pycparser reads (an old MSVC has no `long long`), and a calling convention, a
    `__declspec` or `extern "C"` goes back in front of the name it was on: a variant compiles as the source did."""
    from decompnet.permuter_gen import Restorer

    source = ('extern "C" unsigned __int64 __fastcall Mix(__int64 a, int (__stdcall *cb)(int)) {\n'
              "    return a + 0x10ui64 + cb(1);\n}\n")
    r = Restorer(source)
    assert "__" not in r.source.replace("DD_", "") and "extern" not in r.source and "0x10ULL" in r.source
    variant = "DD_uint64 Mix(DD_int64 a, int (*cb)(int))\n{\n  DD_uint64 t = a + 0x10ULL;\n  return t + cb(1);\n}\n"
    assert r.restore(variant) == ('extern "C" unsigned __int64 __fastcall Mix(__int64 a, int (__stdcall *cb)(int))\n'
                                  "{\n  unsigned __int64 t = a + 0x10ui64;\n  return t + cb(1);\n}\n")
    # a function returning a pointer has its convention after the `*`
    r = Restorer("Vec *__stdcall Make(int n)\n{\n    return 0;\n}\n")
    assert r.restore(r.source) == "Vec *__stdcall Make(int n)\n{\n    return 0;\n}\n"


def test_a_methods_cxx_goes_back_into_each_variant():
    """Most of an x86 game's near misses are methods: `A::f`, templates, references, a local built by its
    constructor and a temporary each become C pycparser reads, and go back as they were in every variant."""
    from decompnet.permuter_gen import Restorer

    source = ("bool Widget::Moved(const Vec2 &to, int n) const\n{\n    Vec2 d(to.x - pos.x, 0.0f);\n"
              "    float *f = &RawAt<float>(this, 0x10);\n    *f = Base::Scale(Vec2(1.0f, 2.0f), n);\n"
              "    return d.x != 0.0f && m_state == Widget::STATE_IDLE;\n}\n")
    r = Restorer(source, {"Vec2", "Widget"})
    assert "::" not in r.source and "<" not in r.source.replace("< ", "") and "&to" not in r.source
    assert r.name("Widget::Moved") == "Widget__DDq__Moved" and "Vec2 d = DDctor(" in r.source
    assert "DDnew_Vec2(1.0f, 2.0f)" in r.source and "const Vec2 *DDref_to" in r.source
    assert r.restore(r.source) == source


def test_cxx_that_isnt_a_constructor_is_left_alone():
    from decompnet.permuter_gen import Restorer

    source = "int f(Vec2 *v, int a)\n{\n    int n = sizeof(Vec2) + g(a) + (a & 3);\n    return (Vec2 *) v != 0;\n}\n"
    assert Restorer(source, {"Vec2"}).source == source


def test_macros_called_with_a_type_and_object_like_ones_are_put_in_the_functions_c():
    """`F(int, 0x20) = v` and `MATCH_VOLATILE bool found` aren't C pycparser reads: 13 of an Xbox game's near
    misses stopped at one. A macro called with values stays a call, for the randomizer to move."""
    from decompnet import permuter_gen as g

    header = "#define MP_RAW(T, off) (*(T *)((char *)this + (off)))\n#define MATCH_VOLATILE volatile\n#define SQ(x) ((x) * (x))\n"
    source = ("void f(char *p)\n{\n    MATCH_VOLATILE bool found = false;\n    F(int, 0x20) = MP_RAW(Widget *, 8)->n;\n"
              "    g(SQ(3), MP_RAW(bool, SQ(2)));\n}\n")
    out = g.expand_in_source(source, header + "#define F(T, off) (*(T *)(p + (off)))\n", {"Widget"})
    assert "volatile bool found" in out and "(*(int *)(p + (0x20))) = (*(Widget * *)((char *)this + (8)))->n" in out
    assert "g(SQ(3), (*(bool *)((char *)this + (SQ(2)))))" in out  # (SQ is called with values: it stays)
    # a macro naming another name reads as the name it is: put in, it renamed the function's own class
    alias = "bool CPlayerCar::Update(int n)\n{\n    return n == LIMIT;\n}\n"
    assert g.expand_in_source(alias, "#define CPlayerCar CPlayerCarView\n#define LIMIT 3\n", set()) == alias


def test_a_bodys_local_of_a_type_the_header_lacks_is_a_type():
    """The SDK's `WAVEFORMATEX format = {0};`, `BOOL wait;`, a type declared where the header doesn't reach: two
    names in a row or `T *p =` start a declaration, never an expression; `a * b;` and `return x;` aren't these."""
    from decompnet import permuter_gen as g

    names = g.type_names("", "void f(int a, int b)\n{\n    WAVEFORMATEX format = { 0 };\n    BOOL wait;\n"
                             "    CLobbyClient *lobby = &g_net.m_lobby;\n    a * b;\n    limit = 1.2f;\n"
                             "    g_3EBF60 = 0;\n    return a;\n}\n")
    for t in ("WAVEFORMATEX", "BOOL", "CLobbyClient"):
        assert f"typedef int {t};" in names
    assert "typedef int a;" not in names and "typedef int return;" not in names
    assert "limi" not in names and "g_3EB" not in names  # (one name isn't two: `limit = 1.2f;`)


@needs_tool
def test_decomp_permuter_reads_an_sdk_local_and_a_macro_with_a_type():
    job = {"header": CXX_HEADER + "#define MP_RAW(T, off) (*(T *)((char *)this + (off)))\n", "family": "base",
           "seed": 1, "count": 8, "function": "Widget::Count",
           "source": "int Widget::Count(int k)\n{\n    BOOL wait = 0;\n    int n = MP_RAW(int, 0x10);\n"
                     "    for (Widget *w = this; w; w = w->next) n += w->mode + k;\n    return n + wait;\n}\n"}
    gen = permute.Generator()
    try:
        job["header"] = permute.permuter_header(job["header"], job["source"])
        variants = gen.batch(job)
    finally:
        gen.close()
    assert variants and all(" Widget::Count(int k)\n" in v for v in variants)


@needs_tool
def test_decomp_permuter_reads_a_method():
    job = {"header": CXX_HEADER, "family": "base", "seed": 1, "count": 8, "function": "Widget::Count",
           "source": "int Widget::Count(const Vec2 &at) const\n{\n    Vec2 d(at.x, pos.y);\n    int n = 0;\n"
                     "    for (Widget *w = this; w; w = w->next) n += w->mode;\n    return n + (int) d.x;\n}\n"}
    gen = permute.Generator()
    try:
        job["header"] = permute.permuter_header(job["header"], job["source"])
        variants = gen.batch(job)
    finally:
        gen.close()
    assert variants and all(" Widget::Count(const Vec2 &at) const\n" in v and "Vec2 d(" in v and "DD" not in v
                            for v in variants)


@needs_tool
def test_decomp_permuter_reads_a_c_function_among_cxx_headers():
    job = {"header": CXX_HEADER + "typedef unsigned __int64 Id;\n",
           "source": "int __fastcall Count(Widget *w, Id id) {\n    int n = 0;\n    while (w) { n += w->mode; w = w->next; }\n"
                     "    return n + (int) id;\n}\n", "function": "Count", "family": "base", "seed": 1, "count": 8}
    gen = permute.Generator()
    try:
        job["header"] = permute.permuter_header(job["header"], job["source"])
        variants = gen.batch(job)
    finally:
        gen.close()
    assert variants and all("int __fastcall Count(Widget *w, Id id)" in v for v in variants)


def test_msvcs_words_leave_a_macros_name_its_value_and_strings_alone():
    """`#define WINAPI __stdcall` lost its name (`#define int f(...)`), `#define STDCALL __stdcall` its value (the
    variants lost the convention), and the newline after the word went with it, joining the next line on."""
    from decompnet.permuter_gen import msvc_as_c

    assert msvc_as_c("#define STDCALL __stdcall\nint STDCALL f(int a);") == \
        "#define STDCALL __stdcall\nint STDCALL f(int a);"
    assert msvc_as_c("#define WINAPI __stdcall\nint WINAPI f(int a);") == "#define WINAPI __stdcall\nint f(int a);"
    assert msvc_as_c('#define DLLEXPORT __declspec(dllexport)\nconst char *s = "__stdcall";\n'
                     "struct __declspec(align(16)) V { float x; };\nint __cdecl\ng(void);") == \
        ('#define DLLEXPORT __declspec(dllexport)\nconst char *s = "__stdcall";\nstruct V { float x; };\nint \n'
         "g(void);")


def test_a_const_method_keeps_its_const_where_it_was():
    """`const` went back after the first `(...)` of the name: `operator()(int a) const` became
    `operator() const(int a)`, and two const overloads gave `const const` on one and none on the other."""
    from decompnet.permuter_gen import Restorer

    for source in ("int Widget::operator()(int a) const\n{\n    return a;\n}\n",
                   "int A::f() const\n{\n    return 1;\n}\nint A::f(int a) const\n{\n    return a;\n}\n"):
        r = Restorer(source, {"Widget", "A"})
        assert not re.search(r"\bconst\b", r.source) and r.restore(r.source) == source


@needs_tool
@pytest.mark.parametrize("header,head", [("#define STDCALL __stdcall\nint STDCALL f(int a);\n", "int STDCALL f(int a)"),
                                         ("#define WINAPI __stdcall\nint WINAPI f(int a);\n", "int WINAPI f(int a)"),
                                         ("#define DLLEXPORT __declspec(dllexport)\nDLLEXPORT int f(int a);\n",
                                          "DLLEXPORT int f(int a)")])
def test_decomp_permuter_reads_a_function_among_msvcs_macros(header, head):
    source = f"{head}\n{{\n    int b = a + 1;\n    return b * 2;\n}}\n"
    job = {"header": permute.permuter_header(header, source), "source": source, "function": "f", "family": "base",
           "seed": 1, "count": 8}
    gen = permute.Generator()
    try:
        variants = gen.batch(job)
    finally:
        gen.close()
    # the macros are put in where the function uses them, and its convention kept (the randomizer may retype what
    # it returns: `__declspec(dllexport) volatile unsigned int f(int a)`)
    convention = r"__declspec\(dllexport\) [^;{]*\bf\(int a\)" if "DLLEXPORT" in head else r"\b__stdcall f\(int a\)"
    assert variants and all(re.search(convention, v) for v in variants)


CXX_GUARDED = "#ifdef __cplusplus\n" + CXX_HEADER.split("class Base")[0] + "#endif\n"


def test_a_cxx_functions_header_keeps_its_cxx_branch(monkeypatch):
    """A C++ function's classes in an `#ifdef __cplusplus` block were taken out (seeds.c_only, which drafts need, as
    they're C), leaving decomp-permuter nothing to read; a C function's header is still C's."""
    net = FakeNet()
    net._context = lambda project_id, unit: {"header": HEADER + CXX_GUARDED, "version": 1}
    fn = {**net.function(), "best_source": "float Vec2::Len()\n{\n    return x + y;\n}\n"}
    jobs = batches(monkeypatch, [])
    ScriptedPermuter(net, {})._permute(AGENT, net.project_row("game"), fn, State())
    assert "struct Vec2" in jobs[0]["header"]
    assert permute.is_cxx("int Widget::Count(int k) { return k; }") and not permute.is_cxx(SOURCE)


@needs_tool
def test_decomp_permuter_reads_a_method_whose_class_is_cxx_only():
    source = "float Vec2::Len()\n{\n    float n = x;\n    return n + y;\n}\n"
    job = {"header": permute.permuter_header(seeds.c_only(CXX_GUARDED, cxx=permute.is_cxx(source)), source),
           "source": source, "function": "Vec2::Len", "family": "base", "seed": 1, "count": 8}
    gen = permute.Generator()
    try:
        variants = gen.batch(job)
    finally:
        gen.close()
    assert variants and all("float Vec2::Len()" in v for v in variants)


EXTERN_C_GUARDED = ("#ifdef __cplusplus\nextern \"C\" {\n#endif\ntypedef struct Player { int hp; } Player;\n"
                    "int Score(Player *p);\n#ifdef __cplusplus\n}\nclass Widget {\npublic:\n    int n;\n"
                    "    int Get(Player *p);\n};\n#endif\n")


def test_a_cxx_header_keeps_the_c_declarations_its_extern_c_block_wraps():
    """`extern "C" {` lost its `extern "C"` (the word's lookahead let the space before `{` through), leaving a bare
    `{ ... };` that pycparser refused, so a method in such a header got no variants at all."""
    from decompnet.permuter_gen import cxx_as_c

    text = cxx_as_c(seeds.c_only(EXTERN_C_GUARDED, cxx=True))
    assert "int Score(Player *p);" in text and "struct Widget" in text and not re.search(r"^\s*\{", text, re.M)


@needs_tool
def test_decomp_permuter_reads_a_method_whose_header_wraps_its_c_in_extern_c(monkeypatch):
    net = FakeNet()
    net._context = lambda project_id, unit: {"header": EXTERN_C_GUARDED, "version": 1}
    source = "int Widget::Get(Player *p)\n{\n    int a = p->hp;\n    return a + n;\n}\n"
    fn = {**net.function(), "name": "Widget::Get", "best_source": source}
    generator = permute.Generator
    jobs = batches(monkeypatch, [])  # the job a turn hands the generator, as the app makes it
    ScriptedPermuter(net, {})._permute(AGENT, net.project_row("game"), fn, State())
    gen = generator()
    try:
        variants = gen.batch({**jobs[0], "count": 8})
    finally:
        gen.close()
    assert variants and all("int Widget::Get(Player *p)" in v for v in variants)


def test_a_c_function_that_says_this_in_a_comment_or_string_isnt_cxx():
    assert not permute.is_cxx('int f(int a)\n{\n    /* this adds one */\n    puts("this");  // this too\n'
                              "    return a + 1;\n}\n")
    assert permute.is_cxx("int Widget::Get(int a)\n{\n    return this->n + a; /* a C one */\n}\n")


@needs_tool
@pytest.mark.parametrize("source", [
    "int f(int a)\n{\n    enum class K { A, B };\n    return a + (int) K::B;\n}\n",
    "int f(int a)\n{\n    std::vector<int> v;\n    v.push_back(a);\n    return v[0];\n}\n",
    "Vec2 Vec2::operator+(const Vec2 &o) const\n{\n    Vec2 r;\n    r.x = x + o.x;\n    return r;\n}\n",
    "int f(int a) noexcept\n{\n    return a + 1;\n}\n",
    "int f(int a) throw()\n{\n    return a + 1;\n}\n",
    "int *f(int a)\n{\n    int *p = new int(a);\n    return p;\n}\n",
])
def test_cxx_decomp_permuter_doesnt_read_yet_falls_back_to_the_simple_rewrites(source):
    """What permuter_gen's docstring says isn't read yet: the generator answers None, and the turn goes on with
    the simple rewrites (test_c_decomp_permuter_cant_read_falls_back_to_the_simple_rewrites)."""
    name = re.search(r"([\w:+]+)\(", source.split("\n")[0]).group(1)
    job = {"header": permute.permuter_header(seeds.c_only(CXX_HEADER, cxx=True), source), "source": source,
           "function": name, "family": "base", "seed": 1, "count": 8}
    gen = permute.Generator()
    try:
        assert gen.batch(job) is None
    finally:
        gen.close()


def test_a_variant_the_project_refuses_is_skipped_and_the_turn_goes_on(monkeypatch):
    """decomp-permuter's inlining writes a helper function beside the function, which a fork's compile_and_diff
    refuses: that refusal ended the function's permuting for good, seconds into its first turn (2 of 3 of an Xbox
    game's near misses). Such a variant is skipped like one that doesn't compile."""
    from decompnet.network import NetworkError

    net = FakeNet()
    helper = "inline s32 g(s32 a) { return a; }\n" + SOURCE
    batches(monkeypatch, [helper, "closer", "same"])
    p = ScriptedPermuter(net, {helper: NetworkError("compile_and_diff takes one function definition"), "closer": 20,
                               "same": 0})
    st = State()
    p._permute(AGENT, net.project_row("game"), net.function(), st)
    assert p.judged == [helper, "closer", "same"] and st.matched == 1


def test_a_turn_whose_every_variant_is_refused_hands_the_refusal_on(monkeypatch):
    """Every variant refused: the C the turn started from is what the project won't take, and _turn leaves the
    function to agents (test_a_source_the_project_refuses_is_one_line_in_the_log)."""
    from decompnet.network import NetworkError

    net = FakeNet()
    batches(monkeypatch, ["a", "b"])
    refusal = NetworkError("send the function definition")
    p = ScriptedPermuter(net, {"a": refusal, "b": refusal})
    with pytest.raises(NetworkError):
        p._permute(AGENT, net.project_row("game"), net.function(), State())
    assert p.judged == ["a", "b"] and net.store.one("SELECT 1 FROM claims") is None  # let go all the same


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
