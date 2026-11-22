"""decomprepair 0.5.600: triangle_arm_calls (a triangle's vertex addresses built in each arm of a halfword-or-byte index
test, each arm calling) reaches the draft repairs and the deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2) built a triangle's three vertex
addresses in three m2c temporaries and called once after the index test. The original calls in each arm with
`verts + tri[k] * 3` (a `f32 *`, three floats a vertex): gcc then cross-jumps the identical tails and the arms schedule
as the target's. From the saved best the three sites together score 5943 -> 1418 in the isolated judge (one site alone is
worse: 7133, 6823); judge-worker replay, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef unsigned short u16;\ntypedef float f32;\n'
SOURCE = HEADER + '''void plane(void *obj, s32 i, void *out)
{
    s32 var_a1;
    s32 var_a2;
    u16 var_v0;
    void *temp_v1;
    void *temp_v2;
    if (M2C_FIELD(obj, u8 *, 0x48) & 4) {
        temp_v1 = M2C_FIELD(obj, u8 **, 0x3C) + (i * 6);
        var_a1 = M2C_FIELD(obj, s32 *, 0x34) + (M2C_FIELD(temp_v1, u16 *, 0) * 0xC);
        var_a2 = M2C_FIELD(temp_v1, u16 *, 2) * 3;
        var_v0 = M2C_FIELD(temp_v1, u16 *, 4);
    } else {
        temp_v2 = M2C_FIELD(obj, u8 **, 0x3C) + (i * 3);
        var_a1 = M2C_FIELD(obj, s32 *, 0x34) + (M2C_FIELD(temp_v2, u8 *, 0) * 0xC);
        var_a2 = M2C_FIELD(temp_v2, u8 *, 1) * 3;
        var_v0 = (u16) M2C_FIELD(temp_v2, u8 *, 2);
    }
    build(out, var_a1, M2C_FIELD(obj, s32 *, 0x34) + (var_a2 * 4), M2C_FIELD(obj, s32 *, 0x34) + (var_v0 * 0xC));
}
'''
FAMILY = 'triangle_arm_calls'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'plane', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    rewritten = [t for t in texts if labels.get(t) == FAMILY]
    assert rewritten and all(isinstance(t, str) for t in texts)
    assert any('u16 *tri = (u16 *)M2C_FIELD(obj, u8 **, 0x3C) + i * 3;' in t and 'var_a1 =' not in t for t in rewritten)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'plane', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}
