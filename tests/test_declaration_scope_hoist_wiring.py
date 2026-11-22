"""decomprepair 0.5.596: declaration_scope_hoist (a scalar or pointer local declared in an inner block moved back to the
function's declarations, between its neighbours by `spXX` offset) reaches the draft repairs and the deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2) had two of its spilled address
locals declared at the top of the block that uses them; with both back among the function's declarations, in name order,
23 differing rows went (5943 -> 5920, the room section's six address slots at the target's offsets). The family reproduces
that edit exactly from the saved best; isolated judge-worker replay, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef float f32;\n'
SOURCE = HEADER + '''s32 cast(f32 *a, void *b)
{
    f32 *sp164;
    f32 *sp16C;
    f32 *sp17C;
    f32 *sp18C;
    s32 count;

    count = 0;
    if (b != NULL) {
        f32 *sp174;
        f32 *sp184;
        sp164 = &a[0];
        sp16C = &a[1];
        sp174 = &a[2];
        sp17C = &a[3];
        sp184 = &a[4];
        sp18C = &a[5];
        count = use(sp164, sp16C, sp174, sp17C, sp184, sp18C);
    }
    return count;
}
'''
FAMILY = 'declaration_scope_hoist'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'cast', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    hoisted = [t for t in texts if labels.get(t) == FAMILY]
    assert hoisted and all(isinstance(t, str) for t in texts)
    assert any('    f32 *sp164;\n    f32 *sp16C;\n    f32 *sp174;\n    f32 *sp17C;\n    f32 *sp184;\n    f32 *sp18C;\n' in t
               for t in hoisted)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'cast', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}
