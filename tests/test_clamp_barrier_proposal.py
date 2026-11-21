"""clamp_difference_barrier reaches the drafts the permuter and the agents' repair step build."""
from decompnet.app import draft_repairs

SOURCE = '''s32 f(u8 *arg0)
{
  u32 x;
  s32 t;
  s32 v;
  x = arg0[0x10];
  t = x - 0x20;
  if (t >= 0)
  {
    v = x - 0x20;
    if (t >= 0x25)
    {
      v = 0x24;
    }
  }
  else
  {
    v = 0;
  }
  return v;
}
'''


def test_family_is_proposed_and_registered():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'clamp_difference_barrier' in set(labels.values())
    assert any('t = (s16) x - 0x20;' in t for t in texts)
    assert 'clamp_difference_barrier' in draft_repairs.AGENT_FAMILIES
