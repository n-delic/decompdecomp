"""Machine drafts (m2c): what they are made from, when they are made again, and how agents get them."""

import shutil
import threading
import time

import pytest

from decompnet import seeds, toolchains
from decompnet.app import drafts
from decompnet.app.engine import RomNetwork

from .test_rom import built  # noqa: F401  (fixture that builds the demo ROM)

SLUG = "demo-quest-64"
needs_demo = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc and ld.lld to build the demo ROM")
needs_m2c = pytest.mark.skipif(toolchains.installed("m2c") is None, reason="needs m2c")


HEADER = """typedef signed int s32;   /* a word */
typedef float f32;
#define COUNT 4
typedef struct Vec { f32 x, y, z; } Vec;
struct Node { struct Node *next; Vec pos; s32 kind; };  /* 0x18 */
enum Kind { KIND_A, KIND_B = 2 };
typedef s32 (*Callback)(struct Node *node, s32 arg);
extern struct Node *g_nodes[COUNT];
extern Callback g_callback;
s32 node_kind(struct Node *node);
void unrelated(Vec *v);
#ifdef __cplusplus
class CThing { public: s32 m_id; };
#else
typedef struct CThing { s32 m_id; } CThing;
#endif
"""


def test_the_header_m2c_reads_leaves_out_what_is_only_for_cplusplus():
    """GTA San Andreas's header keeps its classes in an `#ifdef __cplusplus` block: kept for m2c, it made every
    draft's context fail to parse."""
    text = seeds.c_only(HEADER)
    assert "class CThing" not in text and "typedef struct CThing" in text and "#if" not in text and "#define" in text
    assert seeds.c_only("#ifndef __cplusplus\ns32 c;\n#else\nclass CThing {};\n#endif\n") == "s32 c;\n"
    assert "hidden" not in seeds.c_only("#if 0\ns32 hidden;\n#endif\ns32 shown;\n")


def test_a_function_gets_the_declarations_it_uses_and_what_those_need():
    index = seeds.HeaderIndex(seeds.c_only(HEADER))
    used = [d.text for d in index.used({"node_kind", "lw", "a0"})]
    assert used == ["typedef signed int s32;   /* a word */", "typedef float f32;", "typedef struct Vec { f32 x, y, z; } Vec;",
                    "struct Node { struct Node *next; Vec pos; s32 kind; };  /* 0x18 */",
                    "s32 node_kind(struct Node *node);"]
    by_names = {name: d for d in index.decls for name in d.names}
    assert {"KIND_A", "KIND_B", "Kind", "Callback", "g_nodes", "g_callback", "COUNT", "unrelated"} <= set(by_names)
    assert "COUNT" in by_names["g_nodes"].uses and "Callback" in by_names["g_callback"].uses
    assert not index.used({"nothing_here"})


def test_the_names_of_an_assembly_listing_are_its_symbols():
    asm = ("glabel func_80000400\n/* 400 80000400 3C048001 */  lui        $a0, %hi(D_80010000)\n"
           "/* 404 80000404 0C000200 */  jal        node_kind\n/* 408 80000408 8C840000 */  lw         $a0, 0x0($a0)\n"
           ".L80000410:\n/* 410 80000410 03E00008 */  jr         $ra\n")
    assert seeds.asm_names(asm) == {"func_80000400", "D_80010000", "node_kind"}


@pytest.fixture(scope="module")
def net(built, tmp_path_factory):  # noqa: F811
    ws = tmp_path_factory.mktemp("ws")
    net = RomNetwork(ws / "decomp.db", ws)
    assert net.import_rom(built, confirmed=True, log=lambda *_: None) == SLUG
    return net


def _calling(net):
    """A small function of the demo that calls another one, with its callee's name."""
    proj = net._project(SLUG)
    x = net.xrefs(proj)
    syms = net.symbols(proj["id"])
    for fn in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns", proj["id"]):
        callees = sorted(x["calls"].get(fn["vram"], ()))
        if callees and fn["n_insns"] < 40:
            return fn["name"], syms.label(callees[0])
    raise AssertionError("no function of the demo calls another")


@needs_demo
@needs_m2c
def test_a_draft_is_made_again_when_the_declarations_it_uses_change(net):
    agent, _ = net.local_agent("drafter-test")
    name, callee = _calling(net)
    net.get_function(name, SLUG)
    before = net._function(name, SLUG)
    assert before["seed"] and before["seed_key"] and f"{callee}(" in before["seed"]
    # something it doesn't use: the same draft, not made again
    assert net.edit_context(agent, SLUG, "", "typedef struct Unused { s32 a; } Unused;", "unrelated").startswith("✅")
    assert net._draft_plan(net._function(name, SLUG), net._project(SLUG))[0] == before["seed_key"]
    # its callee's prototype: made again on the next brief, with it
    proto = f"void {callee}(s32 a, s32 b, s32 c);"
    assert net.edit_context(agent, SLUG, "", proto, "a prototype").startswith("✅")
    net.get_function(name, SLUG)
    after = net._function(name, SLUG)
    assert after["seed_key"] != before["seed_key"] and after["seed"] != before["seed"]
    assert net.store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=?", after["id"])["n"] == 0


@needs_demo
def test_drafts_of_different_functions_are_made_side_by_side(net, monkeypatch):
    """One lock for the whole app made agents claiming at once wait for each other's drafts (about 0.3 s each)."""
    running, most, calls = [0], [0], []
    lock = threading.Lock()

    def slow(asm, name, *args, **kw):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
            calls.append(name)
        time.sleep(0.3)
        with lock:
            running[0] -= 1
        return f"void {name}(void) {{\n}}"
    monkeypatch.setattr(seeds, "available", lambda: True)
    monkeypatch.setattr(seeds, "generate_from_asm", slow)
    proj = net._project(SLUG)
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND seed IS NULL LIMIT 4", proj["id"])
    assert len(rows) == 4
    work = [threading.Thread(target=net._seed_for, args=(r, proj)) for r in rows]
    work.append(threading.Thread(target=net._seed_for, args=(rows[0], proj)))  # the same function twice: drafted once
    started = time.monotonic()
    for t in work:
        t.start()
    for t in work:
        t.join()
    assert most[0] >= 3 and time.monotonic() - started < 1.0
    assert sorted(calls) == sorted(r["name"] for r in rows)


@needs_demo
@needs_m2c
def test_every_open_function_gets_a_draft_in_the_background_and_autopilot_tries_the_changed_ones(net, monkeypatch):
    monkeypatch.setattr(drafts, "POLL_SECONDS", 0.2)
    proj = net._project(SLUG)
    auto, _ = net.local_agent("autopilot")
    fn = net.store.one("SELECT * FROM functions WHERE project_id=? AND status='open' AND n_insns > 30 "
                       "ORDER BY n_insns LIMIT 1", proj["id"])
    first = net._autopilot_one(auto, proj, fn["id"])
    assert first in ("near", "far", "fail"), "a function autopilot can't match"
    count = lambda: net.store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=?", fn["id"])["n"]  # noqa: E731
    tried = count()
    assert net._autopilot_one(auto, proj, fn["id"]) == "skip" and count() == tried  # the same draft isn't judged twice
    asked = []
    judge = net._autopilot_one
    monkeypatch.setattr(net, "_autopilot_one", lambda agent, proj, fid: asked.append(fid) or judge(agent, proj, fid))
    st = net.drafter.start(SLUG)
    try:
        for _ in range(1200):
            if st.total and st.made >= st.total and not net.stale_drafts(proj):
                break
            time.sleep(0.1)
        assert not net.stale_drafts(proj), "every open function has a draft made from what it's made of now"
        assert not net.store.one("SELECT 1 FROM functions WHERE project_id=? AND status='open' AND seed IS NULL",
                                 proj["id"])
        # its address range built with other flags: its draft is made again in the background, and autopilot,
        # which tried it before, judges the new one
        asked.clear()
        flags = net.cfg(proj)["compiler"]["flags"]
        net.set_compiler_ranges(SLUG, [[fn["vram"], fn["vram"] + fn["size"]]], net.cfg(proj)["compiler"]["toolchain"],
                                [*flags, "-g0"])
        for _ in range(600):
            if fn["id"] in asked and not net.stale_drafts(net._project(SLUG)):
                break
            time.sleep(0.1)
        assert fn["id"] in asked and not net.stale_drafts(net._project(SLUG))
        assert "-g0" in net.store.one("SELECT source FROM attempts WHERE function_id=? ORDER BY id DESC LIMIT 1",
                                      fn["id"])["source"].split("*/", 1)[0] or count() == tried
    finally:
        net.drafter.stop(SLUG)
        net.set_compiler_ranges(SLUG, [[fn["vram"], fn["vram"] + fn["size"]]], None)
