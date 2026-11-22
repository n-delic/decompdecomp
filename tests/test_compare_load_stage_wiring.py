"""decomprepair 0.5.598: compare_load_stage (the value a result is compared with, read into a local before the store of
that result) reaches the draft repairs and the deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2) compared a helper's distance
with a radius read through a pointer right after storing the distance to a local record. The target loads the radius
first and has the store in the branch's delay slot; with the radius in a local before the store the six rows of that
hunk match (5060 -> 4810). The family reproduces that edit exactly from the saved best; isolated judge-worker replay,
no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef float f32;\ntypedef struct { f32 d8; f32 d16; } Work;\n'
SOURCE = HEADER + '''s32 hit(void *a, void *obj)
{
    Work work;
    f32 dist;
    f32 best;
    s32 found;
    found = 0;
    best = 0.0f;
    dist = measure(a, obj);
    work.d8 = dist;
    if (dist < M2C_FIELD(obj, f32 *, 0x4C)) {
        found = 1;
        best = work.d8;
    }
    return found + (s32) best;
}
'''
FAMILY = 'compare_load_stage'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'hit', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    staged = [t for t in texts if labels.get(t) == FAMILY]
    assert staged and all(isinstance(t, str) for t in texts)
    assert any('__compare_load0 = M2C_FIELD(obj, f32 *, 0x4C);\n    work.d8 = dist;\n    if (dist < __compare_load0)' in t
               for t in staged)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'hit', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}
