"""decomprepair 0.5.588's `const_read_inline` reaches drafts, the agents and the Deep Permuter's beam and local lane.

A KMC GCC 2.7.2 plane-from-three-points routine (N64, 95 instructions, three input vectors and an output plane) sat at
480: the saved source took its vectors as plain struct pointers and named every difference. From that source the
family's child (pointers const, the differences written in the products, one kept as a local) scored 0 in the isolated
judge (judge-worker, no permuter run); `const` alone was 240, the inlining alone 7323 (the output may alias a plain
input). This test uses a synthetic function, not the game's code."""
from decompnet.app import draft_repairs
from decomprepair import const_read_inline

SOURCE = '''s32 plane(Pl *out, V3 *p1, V3 *p2, V3 *p3)
{
  V3 n;
  f32 len;
  f32 py = p1->y;
  f32 pz = p1->z;
  f32 dz1 = p2->z - pz;
  f32 dy1 = p2->y - py;
  f32 dz2 = p3->z - p2->z;
  f32 dy2 = p3->y - p2->y;
  n.x = (dy1 * dz2) - (dz1 * dy2);
  n.y = dz1 * dz2;
  len = g(n.x);
  out->x = n.x;
  out->d = (-out->x) * py - out->y * pz;
  return 1;
}
'''
CHAIN = 'n.x = ((p2->y - p1->y) * (p3->z - p2->z)) - (dz1 * (p3->y - p2->y));'


def _kept_dz1(texts):
    return [t for t in texts if CHAIN in t and '  f32 dz1 = p2->z - p1->z;\n' in t
            and 'Pl *out, const V3 *p1, const V3 *p2, const V3 *p3' in t]


def test_the_family_is_known_to_the_agents():
    assert const_read_inline.FAMILY in draft_repairs.AGENT_FAMILIES


def test_drafts_and_the_beam_propose_the_combination():
    labels = {}
    out = draft_repairs.variants(SOURCE, 'plane', 'mips', labels=labels, limit=None)
    offered = [v for v in out if labels.get(v) == const_read_inline.FAMILY]
    assert _kept_dz1(offered)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from decompnet.app import deep_permuter
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
    offered = [text for family, text in proposals if family == const_read_inline.FAMILY]
    assert _kept_dz1(offered)
