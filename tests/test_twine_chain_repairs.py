"""The twelve repairs of one 719-instruction N64 function's 5060 -> exact chain (gcc 2.7.2), offered through draft_repairs."""
from decompnet.app import draft_repairs

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
SOURCE = '''extern u8 count;
void walk(s32 n)
{
  volatile unsigned int cache;
  u8 *p;
  u8 *q;
  s32 i;
  s32 v;
  s32 t;
  s32 a;

  p = &table;
  i = 0;
  a = 0;
  if (((s32) count) > 0)
  {
    q = p + 0xC2;
    do
    {
      a = 0;
      if (n != 0)
      {
        a = f(i);
      }
      i += 1;
      p += 0x1A0;
      q = p + 0xC2;
    }
    while (i < n);
    i = 0;
  }
  do
  {
    g(i);
    i += 1;
  }
  while (i < 0x20);
  t = a - 0x2000;
  if (a != 0x2000)
  {
    g(t);
  }
  if (a >= 0)
  {
    v = 0x7F0000;
    if (a <= 0x7F0000)
    {
      v = a;
    }
  }
  else
  {
    v = 0;
  }
  g(v);
}
'''


def _labels(source):
    labels = {}
    draft_repairs.variants(source, 'walk', 'mips', '', FLAGS, labels, limit=None)
    return set(labels.values())


def test_the_chain_families_are_offered():
    found = _labels(SOURCE)
    for family in ('loop_head_pointer_giv', 'default_into_else', 'guard_temp_sink', 'clamp_default_scope',
                   'volatile_local_drop', 'narrow_count_copy'):
        assert family in found, family


def test_the_chain_families_are_known_to_the_agents():
    for family in ('loop_head_pointer_giv', 'default_into_else', 'else_into_default', 'stored_value_compare',
                   'narrow_count_copy', 'loop_entry_counter_reset', 'guard_temp_sink', 'product_shift_stage',
                   'clamp_default_scope', 'volatile_local_drop', 'disjoint_local_merge', 'reused_temp_split',
                   'accumulator_chain'):
        assert family in draft_repairs.AGENT_FAMILIES, family
