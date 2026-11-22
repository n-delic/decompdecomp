"""decomprepair 0.5.614: entry_scope_split (a function's opening `do { } while (0)` ended between a pointer chain's two
loads, and a sum's product staged before a scope holding the add and a parameter copy) reaches the draft repairs and the
deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2), saved best 70 with its
opening run already wrapped (entry_scheduling_scope): the split alone 10, the stage alone 60, the pair (one child) 0 in
the isolated judge, reproduced from the saved best and recorded as matched by the app; judge-worker replay, no permuter
run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef float f32;\nextern s32 gA;\nextern s32 gB;\n'
SOURCE = HEADER + '''s32 cast(void **list, f32 *start, f32 *dir, f32 span)
{
    f32 end[3];
    f32 dist;
    void **cursor;
    void **walk;
    void *head;

    do {
    dist = span;
    end[0] = *start + (*dir * dist);
    walk = list;
    end[2] = (M2C_FIELD(dir, f32 *, 8) * dist) + M2C_FIELD(start, f32 *, 8);
    cursor = walk;
    head = M2C_FIELD(M2C_FIELD(walk, void **, 0x20), void **, 0x58);
    gA = 0;
    gB = 0;
    } while (0);
    if (walk != 0) {
        cursor = *cursor;
    }
    return (dist < span) + (s32)head + (s32)cursor + (s32)end[2];
}
'''
FAMILY = 'entry_scope_split'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'cast', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    split = [t for t in texts if labels.get(t) == FAMILY]
    assert split and all(isinstance(t, str) for t in texts)
    assert any('} while (0);\n    head = M2C_FIELD(link_walk, void **, 0x58);' in t for t in split)


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
