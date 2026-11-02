"""ROM join: an agent says two adjacent symbols are one function the analysis cut; once NOT_C_AGENTS agents of
NOT_C_MODELS models agree, the pieces are merged back into one (engine.join_functions). ROM projects previously
had no merge at all (join_functions was fork-only), so over-split fragments got bodied/stubbed. Validated as a
split->join round-trip: splitting a function then joining the pieces restores the original bytes and extent."""

import shutil

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_rom import built  # noqa: F401  (fixture that builds the demo ROM)

pytestmark = pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None or not shutil.which("ld.lld"),
                                reason="needs gcc-2.7.2-kmc and ld.lld to build the demo ROM")

SLUG = "demo-quest-64"
FN = "func_800007C4"      # import ran exit() (noreturn) straight into clamp_s32 at 0x800007D0
TAIL = "func_800007D0"


@pytest.fixture
def net(built, tmp_path):  # noqa: F811  (fresh per test: these tests mutate the function list)
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    assert net.import_rom(built, confirmed=True, log=lambda *_: None) == SLUG
    return net


def _agree(net, a, b, names=("aa-1", "aa-2", "bb-1")):  # 3 agents, 2 models (model_of strips -N)
    out = ""
    for name in names:
        ag, _ = net.local_agent(name)
        out = net.join_functions(ag, SLUG, a, b, "no prologue; uses registers set before it; runs straight in")
    return out


def test_join_restores_a_split_function(net):
    agent, _ = net.local_agent("tester")
    orig = net._function(FN, SLUG)
    orig_size, orig_target = orig["size"], orig["target"]
    assert "split" in net.split_function(agent, SLUG, FN, "0x800007D0")
    assert net._function(FN, SLUG)["size"] < orig_size          # head only now
    out = _agree(net, FN, TAIL)
    assert out.startswith("✅"), out
    joined = net._function(FN, SLUG)
    assert joined["size"] == orig_size, (joined["size"], orig_size)
    assert joined["target"] == orig_target                      # original bytes restored
    assert joined["status"] != "matched"
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?",
                         orig["project_id"], TAIL) is None       # tail merged into the head, removed


def test_vote_below_threshold_does_not_merge(net):
    agent, _ = net.local_agent("tester")
    net.split_function(agent, SLUG, FN, "0x800007D0")
    head_size = net._function(FN, SLUG)["size"]
    ag, _ = net.local_agent("solo-1")
    out = net.join_functions(ag, SLUG, FN, TAIL, "looks joined")
    assert "counted" in out.lower()
    assert net._function(FN, SLUG)["size"] == head_size         # unchanged until enough agree


def test_refuses_non_adjacent(net):
    agent, _ = net.local_agent("tester")
    with pytest.raises(NetworkError):                           # func_80000808 is far away, not the next symbol
        net.join_functions(agent, SLUG, FN, "func_80000808", "not adjacent")


def _relabel(net, name, unit, rom_offset=None):
    with net.store.tx() as db:
        if rom_offset is None:
            db.execute("UPDATE functions SET unit=? WHERE name=?", (unit, name))
        else:
            db.execute("UPDATE functions SET unit=?, rom_offset=? WHERE name=?", (unit, rom_offset, name))


def test_join_across_overlay_units_when_rom_contiguous(net):
    # An over-split cut can leave head and tail in different unit strings though they are one run of an overlay:
    # the head resolves to its source file, the orphaned tail keeps only the overlay label. ROM contiguity proves
    # they are the same run, so the join is allowed across the unit mismatch.
    agent, _ = net.local_agent("tester")
    orig = net._function(FN, SLUG)
    orig_size, orig_target = orig["size"], orig["target"]
    net.split_function(agent, SLUG, FN, "0x800007D0")
    _relabel(net, TAIL, "ovl_demo_fragment")                    # tail now a different unit; rom_offset unchanged
    assert net._function(FN, SLUG)["unit"] != net._function(TAIL, SLUG)["unit"]
    out = _agree(net, FN, TAIL)
    assert out.startswith("✅"), out                             # rom-contiguous => allowed
    joined = net._function(FN, SLUG)
    assert joined["size"] == orig_size, (joined["size"], orig_size)
    assert joined["target"] == orig_target                      # original bytes restored
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?",
                         orig["project_id"], TAIL) is None       # tail merged in, removed


def test_refuses_cross_unit_when_not_rom_contiguous(net):
    # Different unit AND a different ROM region = overlay aliasing (two overlays at one vram), not a continuation.
    agent, _ = net.local_agent("tester")
    net.split_function(agent, SLUG, FN, "0x800007D0")
    tail = net._function(TAIL, SLUG)
    _relabel(net, TAIL, "ovl_elsewhere", rom_offset=(tail["rom_offset"] or 0) + 0x40000)
    ag, _ = net.local_agent("solo-1")
    with pytest.raises(NetworkError):
        net.join_functions(ag, SLUG, FN, TAIL, "aliased, not a continuation")


def test_join_selects_by_rom_run_not_the_shared_vram_window(net):
    # Overlays alias the same vram within one unit (different ROM copies). The merge must pick the pieces by their
    # ROM run, or it would sweep in an aliased copy sitting at the same address+unit and delete it.
    agent, _ = net.local_agent("tester")
    orig = net._function(FN, SLUG)
    pid = orig["project_id"]
    net.split_function(agent, SLUG, FN, "0x800007D0")
    head = net._function(FN, SLUG)
    with net.store.tx() as db:   # a byte-identical alias of the head: same unit+vram, a far-away ROM copy, matched
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                   "status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "alias_head", head["size"], head["n_insns"], head["target"], head["relocs"], head["asm"],
                    None, head["unit"], head["vram"], (head["rom_offset"] or 0) + 0x100000, "matched"))
    net._syms.pop(pid, None)
    out = _agree(net, FN, TAIL)
    assert out.startswith("✅"), out
    assert net._function(FN, SLUG)["size"] == orig["size"]        # head+tail rejoined, nothing else
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, TAIL) is None
    alias = net.store.one("SELECT status FROM functions WHERE project_id=? AND name=?", pid, "alias_head")
    assert alias is not None and alias["status"] == "matched"     # the aliased copy was NOT swept into the merge
