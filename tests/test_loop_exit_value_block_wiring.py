"""decomprepair 0.5.608: loop_exit_value_block (a search loop's not-found value set on its exit path) and
argument_loop_split (a loop variable passed as a call argument gets its own local) reach the draft repairs and the deep
pass's local lane.

A 219-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 285): four search loops carried
`found` round the loop, so it shared no register with the loop's loads; the exit-path form took 285 -> 180, and the
call-argument loop's own variable 180 -> 20 (match after two small edits). Exact judge replay from the saved best,
no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef unsigned short u16;\n'
SOURCE = HEADER + '''s32 find(u8 *data, u16 *key, s32 table)
{
    u8 *entry;
    u8 *found;
    s32 lookup;
    while ((lookup = *key) != 0xffff) {
        entry = data + *(s32 *)data;
        found = 0;
        while (*(s32 *)entry != -1) {
            found = entry;
            if (*(u16 *)(entry + 4) == (u16)lookup) break;
            entry += *(s32 *)entry;
            found = 0;
        }
        if (found) {
            use(*key, found + 8);
        }
        key++;
    }
    while ((lookup = *(key++)) != 0xffff) {
        note(lookup, table);
    }
    return 0;
}
'''
FAMILIES = ('loop_exit_value_block', 'argument_loop_split')


def test_the_families_are_known_to_the_agents():
    assert all(family in draft_repairs.AGENT_FAMILIES for family in FAMILIES)


def test_the_families_are_offered_through_the_draft_repairs():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'find', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert any(labels.get(t) == 'loop_exit_value_block' and 'goto exit_value_1' in t for t in texts)
    assert any(labels.get(t) == 'argument_loop_split' and 'note(lookup_arg, table)' in t for t in texts)


def test_the_families_reach_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'find', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert set(FAMILIES) <= {family for family, _ in proposals}
