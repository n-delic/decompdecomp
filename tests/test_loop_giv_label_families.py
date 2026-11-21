"""The loop label, counter companion and global pointer run families reach the permuter and the agents."""
from decompnet.app import draft_repairs
from decomprepair import counter_companion_repairs, global_pointer_run_repairs, loop_top_label_repairs

SOURCE = '''void f(void)
{
  void *n;
  void *m;
  u32 j;
  char *k;
  j = 0; k = &D_800E6A84;
  n = D_800EDA8C;
  do
  {
    while ((*((u32 *) (((char *) (*((void **) (((char *) n) + 0xF6C)))) + 0))) != (-1U))
    {
      m = *((void **) (((char *) n) + 0xF6C));
      g(m);
      n = D_800EDA8C;
    }
    *((s32 *) (((char *) D_800EDA8C) + 0x118)) = *((s32 *) (((char *) D_800EDA8C) + 0xF68));
    *((s32 *) (((char *) D_800EDA8C) + 0x11C)) = 1;
    *((u8 *) k) = 0;
    k += 0xFF8;
    j++;
  }
  while (8U > j);
}
'''


def test_offered_to_the_permuter_and_the_agents():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert {'loop_top_label', 'counter_companion', 'global_pointer_run'} <= set(labels.values())
    for module in (loop_top_label_repairs, counter_companion_repairs, global_pointer_run_repairs):
        assert set(module.FAMILIES) <= set(draft_repairs.AGENT_FAMILIES)
