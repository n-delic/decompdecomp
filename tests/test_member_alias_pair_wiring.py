"""decomprepair 0.5.613: member_alias_pair (a raw offset store as a structure member beside a global read as a
one-member record) reaches the draft repairs and the deep pass's local lane.

A 144-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 60): the last cursor advance
sat two instructions early because every raw store conflicted with every global. The store as a member and the read
global as a record global gave the original's order (exact judge replay from the saved best, no permuter run)."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\n'
SOURCE = HEADER + '''extern s32 D_A;
extern s32 D_B;
extern void *D_P;
void note(void *, s32);

void f(void *arg)
{
    void *cur;
    s32 count;
    cur = D_P;
    *((s32 *) (((char *) cur) + 0)) = 1;
    *((s32 *) (((char *) cur) + 4)) = 2;
    D_A = D_A + 8;
    count = D_B;
    note(arg, count);
}
'''


def test_the_family_is_known_to_the_agents():
    assert 'member_alias_pair' in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert any(labels.get(t) == 'member_alias_pair' and 'count = D_B.value;' in t and '->value) = 2;' in t
               for t in texts)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'f', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert 'member_alias_pair' in {family for family, _ in proposals}
