"""decomprepair 0.5.594: array_declaration_scope_sink (a top-level array and an unused volatile scratch declared at the
start of the one block that uses them) reaches the draft repairs and the deep pass's local lane. Both already call
`declaration_scope_sink_repairs.variants`, which returns the new children, so this pins that they do.

Measured once on an 842-instruction N64 lit-actor drawer (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, scoring 2):
the saved best 10895 -> 10702, every local at the target's frame offset (the byte locals at 0x20(sp), the vector at
0x30(sp)); isolated judge-worker replay from the saved best, no permuter run."""
from decompnet import seeds
from decompnet.app import deep_permuter, draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned char u8;\ntypedef float f32;\ntypedef unsigned long long u64;\n'
SOURCE = HEADER + '''void draw(u8 *arg0, void *arg1, s32 kind)
{
  volatile u64 padding;
  u8 sp20;
  u8 sp21;
  f32 sp30[3];
  find(arg0, &sp20, &sp21);
  switch (kind)
  {
    case 1:
      break;
    case 2:
      sp30[0] = *(f32 *) (arg0 + 0x24);
      rotate(arg1, sp30);
      break;
  }
}
'''
FAMILY = 'array_declaration_scope_sink'


def test_the_family_is_known_to_the_agents():
    assert FAMILY in draft_repairs.AGENT_FAMILIES


def test_the_family_is_offered_through_the_draft_repairs_with_the_shape_of_the_others():
    labels = {}
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'draw', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    sunk = [t for t in texts if labels.get(t) == FAMILY]
    assert sunk and all(isinstance(t, str) for t in texts)
    assert any(t.index('f32 sp30[3];') > t.index('switch (kind)') and t.index('volatile u64 padding;') > t.index('switch (kind)')
               for t in sunk)


def test_the_family_reaches_the_deep_passes_local_lane():
    import pytest

    from tests.test_deep_parallel import Log, setup
    monkeypatch = pytest.MonkeyPatch()
    try:
        net = setup(monkeypatch)
        proj = net.project_row('game')
        fn = {**net.function(), 'name': 'draw', 'asm': '', 'best_source': SOURCE}
        search = deep_permuter.Search(net, 1, 6, Log())
        proposals, _ = search._local_proposals(SOURCE, proj, fn, [])
    finally:
        monkeypatch.undo()
    assert FAMILY in {family for family, _ in proposals}
