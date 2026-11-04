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


def _three_pieces(net, agent):
    """Cut FN into head | middle | tail and return (middle_name, tail_name). The real shape behind this is a
    branch whose delay-slot NOP became its own pad symbol, then the tail that runs on -- three adjacent symbols."""
    net.split_function(agent, SLUG, FN, "0x800007D0")             # head FN | func_800007D0
    tail = net._function(TAIL, SLUG)
    assert tail["size"] >= 8, tail["size"]                        # room to cut a middle piece off the tail
    mid_addr = tail["vram"] + 4                                   # one instruction in
    net.split_function(agent, SLUG, TAIL, f"0x{mid_addr:08X}")    # middle func_800007D0 | tail func_<mid_addr>
    return TAIL, f"func_{mid_addr:08X}"


def test_two_adjacent_joins_reassemble_a_three_piece_split(net):
    # A join is to the very next symbol, so a head | middle | tail cut is put back with two joins -- head+middle,
    # then head+tail. The middle is absorbed and the original bytes and extent come back. This is the workflow a
    # branch's delay-slot pad plus a trailing tail actually takes.
    agent, _ = net.local_agent("tester")
    orig = net._function(FN, SLUG)
    orig_size, orig_target, pid = orig["size"], orig["target"], orig["project_id"]
    middle, tail2 = _three_pieces(net, agent)
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, tail2) is not None
    assert net._function(FN, SLUG)["size"] < orig_size            # head only, after two cuts
    assert _agree(net, FN, middle).startswith("✅")               # 1st join: head + middle (the pad)
    assert _agree(net, FN, tail2).startswith("✅")                # 2nd join: head + tail, now adjacent
    joined = net._function(FN, SLUG)
    assert joined["size"] == orig_size, (joined["size"], orig_size)
    assert joined["target"] == orig_target                        # original bytes restored, middle absorbed
    for gone in (middle, tail2):
        assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, gone) is None


def test_refuses_a_join_that_skips_a_symbol_between(net):
    # With head | middle | tail, joining the head straight to the tail is refused: a join is to the very next
    # symbol, so the middle must be joined first. This is why the three-piece case needs two joins, not one.
    agent, _ = net.local_agent("tester")
    _, tail2 = _three_pieces(net, agent)
    with pytest.raises(NetworkError):                             # the middle sits between FN and tail2
        net.join_functions(agent, SLUG, FN, tail2, "skip the middle piece")


def test_split_after_a_join_clears_stale_votes(net):
    # Undo-by-split must really undo. After a join fires, its votes stay in the head's meta; splitting the function
    # again must clear them, or a single fresh vote would re-fire the join at once (the names repeat on a re-split).
    agent, _ = net.local_agent("tester")
    pid = net._function(FN, SLUG)["project_id"]
    net.split_function(agent, SLUG, FN, "0x800007D0")
    assert _agree(net, FN, TAIL).startswith("✅")               # head+tail joined (3 agents, 2 models)
    net.split_function(agent, SLUG, FN, "0x800007D0")           # split it again -> TAIL recreated under its name
    ag, _ = net.local_agent("solo-1")
    out = net.join_functions(ag, SLUG, FN, TAIL, "one fresh vote")
    assert "counted" in out.lower(), out                        # a single vote must NOT re-join
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, TAIL) is not None  # still split


def test_join_keeps_an_alias_symbol_at_the_tails_address(net):
    # The absorbed tail's symbol is removed; an aliased copy at the same vram (a different overlay instance) must
    # keep its own symbol -- delete by name AND address, not address alone.
    agent, _ = net.local_agent("tester")
    pid = net._function(FN, SLUG)["project_id"]
    net.split_function(agent, SLUG, FN, "0x800007D0")
    tail = net._function(TAIL, SLUG)
    with net.store.tx() as db:   # an aliased copy of the tail: same vram, a far ROM copy, its own name+symbol
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                   "status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "alias_tail", tail["size"], tail["n_insns"], tail["target"], tail["relocs"], tail["asm"],
                    None, tail["unit"], tail["vram"], (tail["rom_offset"] or 0) + 0x100000, "asm"))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "alias_tail", tail["vram"], tail["size"], "func"))
    net._syms.pop(pid, None)
    assert _agree(net, FN, TAIL).startswith("✅")
    sym = net.store.one("SELECT 1 FROM symbols WHERE project_id=? AND name=? AND address=? AND kind='func'",
                        pid, "alias_tail", tail["vram"])
    assert sym is not None       # the alias kept its symbol; only the absorbed tail's was removed


def test_apply_join_refuses_a_tail_that_matched_since_the_vote(net):
    # Defense in depth, as the saved-layout join does: if a piece matches by the time the merge runs, refuse rather
    # than delete a matched function. (The per-call guard checks first/last; a concurrent match can still slip in.)
    agent, _ = net.local_agent("tester")
    proj = net._project(SLUG)
    net.split_function(agent, SLUG, FN, "0x800007D0")
    first, last = net._function(FN, SLUG), net._function(TAIL, SLUG)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (last["id"],))
    with pytest.raises(NetworkError):
        net._apply_join(proj, first, last, agent, ["aa-1", "aa-2", "bb-1"])
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?",
                         first["project_id"], TAIL) is not None   # the matched tail was not deleted
