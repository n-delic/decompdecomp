"""decomprepair 0.5.575's product_shift_hoist and the widened accumulator_chain reach drafts and the Deep Permuter's
beam. On a 719-instruction KMC N64 function rebuilt from its match with two chain rows undone (2120), the hoist gives
205 and the chain fold 0; the shapes below are theirs, renamed."""
from decompnet.app import draft_repairs

SOURCE = '''void f(char *p, s16 *t)
{
  s32 acc;
  s32 tmp;
  s32 a;
  s32 k;
  tmp = ((*((s32 *) (p - 0x92))) >> 7);
  a = *((s32 *) (p - 0x8A));
  acc = tmp * t[1];
  acc = (acc >> 7) * t[2];
  k = 0x348;
  if (p[3] != 0)
  {
    k = 0x370;
  }
  acc = (acc >> 7) * t[k];
  g(acc, a);
}
'''


def test_both_children_are_offered_and_known_to_agents():
    labels = {}
    found = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert any(labels.get(t) == 'product_shift_hoist' and 'acc = shift_stage * t[k];' in t for t in found)
    assert any(labels.get(t) == 'accumulator_chain' and 'acc = *((s32 *) (p - 0x92));' in t for t in found)
    assert 'product_shift_hoist' in draft_repairs.AGENT_FAMILIES
