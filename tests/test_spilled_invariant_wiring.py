"""The spilled-invariant families (decomp-repair spilled_invariant_repairs) reach the drafts and the Deep Permuter's beam."""
from decompnet.app import draft_repairs

SOURCE = """\
void *heap(s32);
s32 fill(s32 *, s32, s32);
void *f(s32 n, s32 flags, s32 skip)
{
  union
  {
    u8 bytes[3192];
    struct
    {
      s32 value;
    } view_sp10;
    struct
    {
      u8 pad[16];
      f32 value;
    } view_sp20;
    struct
    {
      u8 pad[20];
      f32 value;
    } view_sp24;
    struct
    {
      u8 pad[3172];
      s32 value;
    } view_spC74;
  } frame;
  f32 a;
  f32 b;
  void *p;
  void *base;
  int i;
  base = heap(n);
  i = 0;
  if (n > 0)
  {
    do { do { frame.view_spC74.value = n - 1; } while (0); p = base; } while (0);
    do
    {
      fill(&frame.view_sp10.value, i, 0);
      b = frame.view_sp24.value;
      a = frame.view_sp20.value;
      if ((skip == 0) && (i == frame.view_spC74.value))
      {
        a = 1.0f;
      }
      i += 1;
      *((f32 *) (((char *) p) + 0xC)) = a; p = p + 4;
    }
    while (n > i);
  }
  return base;
}
"""
FAMILIES = ('spilled_view_local', 'late_constant_local', 'walk_index_form', 'nested_and_if',
            'spilled_invariant_combined')


def test_a_frame_view_that_is_a_spilled_invariant_is_proposed_as_a_local_and_as_the_combined_child():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    proposed = {text: family for text, family in labels.items() if family in FAMILIES}
    assert set(proposed.values()) == set(FAMILIES) and all(text in texts for text in proposed)
    combined = next(text for text, family in proposed.items() if family == 'spilled_invariant_combined')
    assert 'spill_C74' in combined and 'late_zero' in combined and '((f32 *) base)[i + 3]' in combined
    assert set(FAMILIES) | {'address_load_order', 'mask_test_order'} <= set(draft_repairs.AGENT_FAMILIES)
