"""decomprepair 0.5.606: entry_scheduling_scope (a function's opening run of plain assignments wrapped in a
`do { } while (0)`, a barrier for the first scheduler, with narrow stack parameters read through locals) reaches the draft
repairs and the deep pass's local lane.

An 851-instruction N64 ray test (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2): the parameter spills came out as
`sw a0; sw a3; ...; sw a2` where the target has them first in order. The run of nine opening statements wrapped in one went
565 -> 250 and, with the `u8` stack parameter read through a local set in the run, 155 in the isolated judge, reproduced
exactly from the saved best; judge-worker replay, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef float f32;\n'
SOURCE = HEADER + '''s32 cast(s32 (*cb)(f32 *), void **list, f32 *start, f32 *dir, void *out, f32 span, void *plane, u8 flag, void *skip)
{
    f32 dist;
    f32 ex;
    f32 ey;
    void **cursor;
    dist = span;
    ex = *start + (*dir * dist);
    ey = start[1] + (dir[1] * dist);
    cursor = list;
    if (cursor != 0) {
        do {
            if (flag != 0) {
                dist = cb(start) & 0xFF;
            }
            cursor = *cursor;
        } while (cursor != 0);
    }
    return dist < span;
}
'''
FAMILY = 'entry_scheduling_scope'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'cast', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    wrapped = [t for t in texts if labels.get(t) == FAMILY]
    assert wrapped and all(isinstance(t, str) for t in texts)
    assert any('    do {\n    dist = span;' in t for t in wrapped)
    assert any('copy_flag = flag;' in t for t in wrapped)


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


def test_the_first_load_staged_child_is_offered_through_the_draft_repairs():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'cast', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert any(labels.get(t) == FAMILY and 'staged_dir = *dir;' in t for t in texts)
