"""decomprepair 0.5.586: edits that only pay off together reach the draft repairs, the deep pass and the local lane."""
from decompnet import seeds
from decompnet.app import draft_repairs, local_experiments

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\n'
SOURCE = HEADER + '''s32 g();
void h();
void k();
void f(s32 *arg0) {
  s32 *slot;
  s32 *temp;
  s32 *next;
  s32 *res;
  s32 *keep;
  do
  {
    res = NULL;
    if (arg0 != NULL)
    {
      temp = &arg0[g(1)]; next = &temp[g(1)]; slot = temp;
      h(next);
      do
      {
        k(res);
      }
      while (0);
      keep = slot;
      h(keep);
    }
  }
  while (0);
}
'''
NEW = ('weight_scope_unwrap', 'spill_slot_copy_collapse', 'init_sink_into_guard', 'weight_combination')


def test_families_are_known_to_the_agents_the_deep_pass_and_the_local_lane():
    for family in NEW + ('goto_nested_entry_else',):
        assert family in draft_repairs.AGENT_FAMILIES
        assert family in local_experiments.FAMILIES  # the local lane's pool keys: its candidates come from draft_repairs.variants


def test_the_combination_is_offered_the_way_the_deep_pass_and_local_lane_ask_for_it():
    labels = {}
    # deep_permuter.children and local_experiments both call draft_repairs.variants(..., limit=None) and read labels
    texts = draft_repairs.variants(seeds.M2C_MACROS + SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    combined = [t for t in texts if labels.get(t) == 'weight_combination']
    assert combined, sorted(set(labels.values()))
    everything = [t for t in combined if 'while (0)' not in t and 'slot = &arg0[g(1)]' in t]
    assert everything and 'slot = temp' not in everything[0]
    assert {labels[t] for t in texts} >= {'spill_slot_copy_collapse'}
    assert all(isinstance(t, str) for t in texts)
