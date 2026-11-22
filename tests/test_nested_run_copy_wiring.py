"""decomp-repair 0.5.641's `goto_nested_run_copy` and `switch_gap_goto` reach the drafts and the Deep Permuter's beam.

Both came from one function (a KMC GCC 2.7.2 state update, 40 -> 0): a run after a label in the chain's last arm copied
into the arm that jumps to it with every step in one child, and the one jump-table entry the target pointed somewhere of
its own. The first is in `join_tail_arm_split_repairs.variants`, the second in `switch_tables.variants`, which the app
already calls with the target's tables; this checks the route with synthetic functions."""
from decompnet.app import draft_repairs

FLAGS = ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2')

CHAIN = '''void f(void *r, s32 s)
{
  union
  {
    void *pointer;
    s32 value;
  } phase;
  f32 x;
  f32 z;
  f32 u;
  f32 w;
  f32 dx;
  f32 dz;
  s32 k;
  s32 mode;
  void *q;
  if (s == 1)
  {
    dx = g_a - g_b;
    dz = g_c - g_d;
    k = 3;
  }
  else
    if (s == 2)
  {
    phase.pointer = g_rec;
    do
    {
      x = g_px;
      z = g_pz; u = *((f32 *) (((char *) phase.pointer) + 0x1A0)); w = *((f32 *) (((char *) phase.pointer) + 0x1A8)); k = *((s32 *) (((char *) phase.pointer) + 0x18C)); mode = 0;
      *((s32 *) (((char *) phase.pointer) + 0xCC)) = 0;
    }
    while (0);
    goto join;
  }
  else
  {
    mode = 0;
    if (s == 3)
    {
      do { q = g_rec; x = g_px; z = g_pz; u = *((f32 *) (((char *) q) + 0x1A0)); w = *((f32 *) (((char *) q) + 0x1A8)); k = *((s32 *) (((char *) q) + 0x18C)); *((s32 *) (((char *) q) + 0xCC)) = 0; } while (0);
    }
    else
    {
      return;
    }
    join:
    dz = w - z;
    dx = u - x;
    g_state = s + 1;
    k = k >> 12;
  }
  out:
  phase.value = use(dx, dz);
  g_out = phase.value + k + mode;
}
'''

SWITCH = '''void f(s32 s)
{
  switch (s - 2)
  {
    case 9:
      g();
      goto done;
    case 0:
    case 3:
      h();
      break;
    default:
      k();
      return;
  }
  return;
  done:
  m();
}
'''


def test_the_nested_run_copy_is_offered_with_every_step_in_one_child():
    assert 'goto_nested_run_copy' in draft_repairs.AGENT_FAMILIES
    labels = {}
    out = draft_repairs.variants(CHAIN, 'f', 'mips', '', FLAGS, labels, limit=None)
    copied = [v for v in out if labels.get(v) == 'goto_nested_run_copy']
    assert copied
    assert any('x_w1' in v and 'phase.pointer' not in v and 'while (0)' not in v and v.count('g_state = s + 1;') == 2
               for v in copied)


def test_the_jump_table_gap_case_is_offered_from_the_targets_table():
    assert 'switch_gap_goto' in draft_repairs.AGENT_FAMILIES
    table = (2, [0x10, 0x30, 0x30, 0x10, 0x30, 0x30, 0x30, 0x50, 0x30, 0x20])
    labels = {}
    out = draft_repairs.variants(SWITCH, 'f', 'mips', '', FLAGS, labels, limit=None, tables=[table])
    assert any(labels.get(v) == 'switch_gap_goto' and 'case 7: goto done;' in v for v in out)
