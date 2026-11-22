"""decomprepair 0.5.601: arm_variable_share (the counters of an `if`/`else`'s sibling loops, and the indices derived from
them, written as one variable) reaches the draft repairs and the deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2): m2c gave the halfword-table loop
and the byte-table loop a counter apiece and an index apiece; the original had one of each. Shared, the counter's priority
(floor_log2(uses) * uses / length) passes the mesh pointer's and every saved register falls where the target has it. Merging one
pair alone made the registers worse; every pair at once is one child. From the pre-merge state the child scores 565 against
830; isolated judge-worker replay, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef unsigned short u16;\n'
SOURCE = HEADER + '''s32 scan(u8 *mesh, s32 flag)
{
    s32 var_s0;
    s32 var_s0_2;
    s32 var_s5;
    s32 var_s5_2;
    u16 *wide;
    u8 *narrow;
    s32 best;
    best = -1;
    if (flag & 4) {
        wide = (u16 *)mesh;
        var_s0 = 0;
        do {
            var_s5 = var_s0 & 0xFFFF;
            if (wide[var_s5] > 3) {
                best = var_s5;
            }
        } while ((u16) ++var_s0 < 9);
    } else {
        narrow = mesh;
        var_s0_2 = 0;
        do {
            var_s5_2 = var_s0_2 & 0xFFFF;
            if (narrow[var_s5_2] > 3) {
                best = var_s5_2;
            }
        } while ((u16) ++var_s0_2 < 9);
    }
    return best;
}
'''
FAMILY = 'arm_variable_share'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'scan', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    shared = [t for t in texts if labels.get(t) == FAMILY]
    assert shared and all(isinstance(t, str) for t in texts)
    assert any('var_s0_2' not in t and 'var_s5_2' not in t for t in shared)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'scan', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}
