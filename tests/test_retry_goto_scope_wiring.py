"""decomprepair 0.5.566's retry_goto_scope reaches the draft repair path.

A rejection loop (pick an entry, compare it with every slot but the owner's own, start over when one is too close) in
a 154-instruction N64 function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) stood at 475 as m2c wrote it. Its skip
join written as an `if` scored 480 and the retry `goto` pinned in a one-shot `do { } while (0)` alone 475; both
together were exact. The shape below is the function's, with other names."""
from decompnet.app import draft_repairs

SOURCE = """\
extern s32 D_count;
extern s32 D_slots[];
extern s32 D_pick(s32);
extern f32 D_far(void *, s32);
void walk(void *arg0)
{
  s16 slot;
  s16 next;
  s32 pick;
  retry:
  slot = 0;
  pick = D_pick(8);
  if (D_count > 0)
  {
    for (;;)
    {
      if ((slot == (*((s8 *) (((char *) arg0) + 0x182))))) { next = slot + 1; goto skip; }
      next = slot + 1;
      if (!(!(D_far(arg0, D_slots[slot] + 0x24) < 3.0f))) goto retry;
      skip:
      slot = next;
      if (next >= D_count) break;
    }
  }
}
"""


def test_the_family_is_proposed_labelled_and_known_to_the_agents():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'walk', 'mips', labels=labels, limit=None)
    assert 'retry_goto_scope' in draft_repairs.AGENT_FAMILIES
    exact = [text for text in result if labels.get(text) == 'retry_goto_scope' and 'do { goto retry; } while (0);' in text
             and 'skip' not in text]
    assert exact and 'if (slot != (*((s8 *) (((char *) arg0) + 0x182))))' in exact[0]


def test_another_architecture_is_not_offered_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'walk', 'x86', labels=labels, limit=None)
    assert 'retry_goto_scope' not in labels.values()
