"""decomprepair 0.5.593's `float_pun_depun` reaches drafts, the agents and the Deep Permuter's beam and local lane.

A KMC GCC 2.7.2 ray-and-triangle test (N64, 178 instructions) sat at 275 with only register rows: its saved source
forced five float locals into integer registers with a float/integer union. From that source the family's children
went 100 (the locals as plain floats) and 0 (also with the three vector differences declared before the loaded
components) in the isolated judge (judge-worker, no permuter run). This test uses a synthetic function, not the
game's code."""
from decompnet.app import draft_repairs
from decomprepair import counted_loop_shapes, float_pun_depun

SOURCE = '''typedef struct { f32 x, y, z; } Vec3;
typedef union { f32 f; u32 i; } FU;

s32 test(Vec3 *a, Vec3 *b, Vec3 *c)
{
    Vec3 e1;
    u32 e2[2];
    FU ex, ey, dx, dy;
    f32 dz, e1x, e1y;
    e1x = b->x - a->x;
    e1.x = e1x;
    e1y = b->y - a->y;
    e1.y = e1y;
    ex.f = c->x - a->x;
    e2[0] = ex.i;
    ey.f = c->y - a->y;
    e2[1] = ey.i;
    dz = ((const volatile Vec3 *)b)->z;
    dy = ((FU *)b)[1];
    dx = ((FU *)b)[0];
    return (dy.f * ex.f - dz * ey.f + dx.f) * e1x + e1y > 0.0;
}
'''
MOVED = '    f32 e1x, e1y;\n    f32 ex, ey, dx, dy;\n    f32 dz;\n'


def _moved(texts):
    return [t for t in texts if MOVED in t and 'e2[0] = ex;' in t and 'dy = ((f32 *)b)[1];' in t]


def test_the_family_is_known_to_the_agents():
    assert float_pun_depun.FAMILY in draft_repairs.AGENT_FAMILIES


def test_counted_loop_shapes_of_the_same_release_is_wired_too():
    assert set(counted_loop_shapes.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
    assert counted_loop_shapes.variants('void f(void) { }', 'f') == []


def test_drafts_and_the_beam_propose_the_chain():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'test', 'mips', labels=labels, limit=None)
    offered = [v for v in out if labels.get(v) == float_pun_depun.FAMILY]
    assert _moved(offered)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'test', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    offered = [text for family, text in proposals if family == float_pun_depun.FAMILY]
    assert _moved(offered)
