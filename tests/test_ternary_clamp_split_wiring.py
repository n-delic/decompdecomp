"""decomprepair 0.5.612: ternary_clamp_split (a `v = c ? v : 0;` clamp as `t = v; if (!c) t = 0;`, later reads on `t`)
reaches the draft repairs and the deep pass's local lane.

A 114-instruction N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 435): a frame index clamped
before two call arms. The ternary (with or without a copy back) put the `and` in the argument register; the copy and
guarded default kept the target's two pseudos: exact from the saved best. Exact judge replay, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\n'
SOURCE = HEADER + '''void use(s32 *, s32, s32);
void note(s32, s32);

void f(s32 *p, s32 index)
{
    s32 count, next;
    count = p[1];
    next = index + 1;
    next = next < count ? next : 0;
    if (p[0]) {
        use(p, index, next);
        return;
    }
    note(index, next);
}
'''


def test_the_family_is_known_to_the_agents():
    assert 'ternary_clamp_split' in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    assert any(labels.get(t) == 'ternary_clamp_split' and 'next_clamped = next;' in t and 'use(p, index, next_clamped)' in t
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
    assert 'ternary_clamp_split' in {family for family, _ in proposals}
