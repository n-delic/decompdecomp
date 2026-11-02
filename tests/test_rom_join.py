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
