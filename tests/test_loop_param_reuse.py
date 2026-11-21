"""The loop_param_reuse families reach the drafts the permuter and the agents' repair step build."""
from decompnet.app import draft_repairs

SOURCE = '''extern char T[];
void g(u8, u8, u8);
s32 h(void);
s32 f(s32 arg0, s32 arg1)
{
  s32 result = -1;
  s32 handle;
  s32 idx;
  u32 low;
  handle = h();
  if (handle != -1)
  {
    low = arg1 & 0x7f;
    while (handle != -1) {
    idx = handle & 0xff;
    arg1 = idx * 0x1a0;
    if (*((s32 *) (T + arg1)) != handle)
    {
      return result;
    }
    result = (u8) idx;
    g(0x84, result, T[arg1]);
    g(0x85, result, low & 0xff);
    handle = *((s32 *) (T + (idx * 0x1a0)));
    result = 0;
    }
  }
  return result;
}
'''


def test_both_families_are_proposed_and_registered():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert {'reused_param_alias', 'dead_call_arg_temp'} <= set(labels.values())
    assert {'reused_param_alias', 'dead_call_arg_temp'} <= set(draft_repairs.AGENT_FAMILIES)
