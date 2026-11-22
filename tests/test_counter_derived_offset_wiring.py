"""decomprepair 0.5.564's counter_derived_offset reaches the draft repair path: an 86-instruction KMC N64 function with
a table walk stepped by an explicit `offset += 4` stood at 115 and went 20 with the offset written off the counter
(0 with the existing one_shot_unwrap on top; each a family, the chain reproducible from the saved source)."""
from decompnet.app import draft_repairs

NAME = 'find'

SOURCE = """\
s32 find(u16 id, u8 flag)
{
  s32 i;
  s32 result;
  char *p;
  if (gActive)
  {
    {
      s32 offset = 0;
      for (i = 0; i < sCount; i++, offset += 4)
      {
        p = *((char **) (((char *) sTable) + offset));
        if ((*((u16 *) (p + 4))) == id)
        {
 do { if (flag) { result = run(p); } else { lock(); result = run(p); unlock(); } return result; } while (0);
        }
      }
    }
  }
  return -1;
}
"""


def test_the_offset_step_is_proposed_labelled_and_known():
    labels = {}
    result = draft_repairs.variants(SOURCE, NAME, 'mips', labels=labels, limit=None)
    assert 'counter_derived_offset' in set(labels.values())
    assert 'counter_derived_offset' in draft_repairs.AGENT_FAMILIES
    shifted = next(t for t in result if labels.get(t) == 'counter_derived_offset')
    assert 's32 offset' not in shifted and '(i << 2)' in shifted and 'for (i = 0; i < sCount; i++)' in shifted
