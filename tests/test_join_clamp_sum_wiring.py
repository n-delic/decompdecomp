"""The three steps that closed a KMC gcc 2.7.2 menu function reach the draft path and the Deep Permuter's lane.

decomp-repair's join_clamp_sum_repairs: a byte clamp compared as `v < 0xFFU`, a pointer copy made before a branch
diamond, and a narrowed halfword sum written full width. The beam runs `draft_repairs.variants`, so one test on it
covers drafts and the permuter."""
from decompnet.app import draft_repairs

SOURCE = '''s32 f(void *arg0, void *entry, void *dst)
{
  u8 temp_v1;
  u8 var_s1_2;
  void *temp_a0;
  void *temp_v1_2;
  f32 temp_fv1;
  s32 var_v1;
  temp_v1 = *((u8 *) (((char *) arg0) + 0x30));
  if ((temp_v1 + 1) < (0xFFU + 1))
  {
    var_s1_2 = temp_v1;
  }
  else
  {
    var_s1_2 = 0xFF;
  }
  temp_v1_2 = *((void **) (((char *) entry) + 8));
  if (temp_v1_2 != NULL)
  {
    temp_fv1 = *((f32 *) (((char *) arg0) + 0xC));
    if (!(temp_fv1 >= 2147483648.0f))
    {
      var_v1 = (s32) temp_fv1;
    }
    else
    {
      var_v1 = ((s32) (temp_fv1 - 2147483648.0f)) | 0x80000000;
    }
    *((u8 *) (((char *) temp_v1_2) + 0x29)) = var_v1;
    temp_a0 = *((void **) (((char *) entry) + 8));
    *((s16 *) (((char *) temp_a0) + 0x22)) = (s16) (var_v1 + (*((u16 *) (((char *) entry) + 0x1A))));
  }
  return var_s1_2;
}
'''
FAMILIES = ('plus_one_clamp_compare', 'copy_across_join', 'wide_sum_temp')


def test_the_families_are_known_to_agents():
    for family in FAMILIES:
        assert family in draft_repairs.AGENT_FAMILIES


def test_each_step_is_offered_by_the_draft_repairs_the_permuter_runs():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', '', ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2'], labels, limit=None)
    texts = {family: [text for text, label in labels.items() if label == family] for family in FAMILIES}
    assert any('if (temp_v1 < 0xFFU)' in text for text in texts['plus_one_clamp_compare'])
    assert any('temp_a0 = temp_v1_2;' in text and 'temp_a0) + 0x29)) = var_v1;' in text for text in texts['copy_across_join'])
    assert any('wide_sum = var_v1 +' in text and '(s16) wide_sum;' in text for text in texts['wide_sum_temp'])
