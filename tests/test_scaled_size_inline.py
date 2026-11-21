"""scaled_size_inline reaches the drafts the permuter and the agents' repair step build."""
from decompnet.app import draft_repairs

SOURCE = '''extern s16 gFlag;
extern s32 gBuf;
s32 heapAlloc(s32, s32);
extern s32 *gSlots[];
void f(void)
{
  s32 size;
  s32 buf;
  s32 i;
  size = 0x800;
  if (gFlag != 0)
  {
    size = 0x4000;
  }
  size *= 8;
  gBuf = heapAlloc(size, 0);
  for (i = 0; i < 3; i++)
  {
    if (gSlots[i] != 0)
    {
      buf = gBuf;
      gSlots[i][0] = buf;
      gSlots[i][1] = (unsigned short) (buf + size);
    }
  }
}
'''


def test_family_is_proposed_and_registered():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'scaled_size_inline' in set(labels.values())
    assert any('heapAlloc((size * 8), 0)' in t and 'size *= 8;' not in t for t in texts)
    assert 'scaled_size_inline' in draft_repairs.AGENT_FAMILIES
