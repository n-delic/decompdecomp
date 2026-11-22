"""decomp-repair 0.5.604's `goto_run_copy` and the volatile-tolerant liveness reach the repairs the deep pass tries.

From a KMC-compiled N64 save-slot state machine (gcc 2.7.2 -O2 -G8, 624 instructions, 1140 -> 0): arms entered by a
switch of gotos shared a continuation, and a table pointer set at each arm head was one multi-set pseudo. Writing
the continuation out in each arm with a pointer of its own made each a single-set pseudo (placed beside its use by
the first scheduler) and let cross-jumping merge the identical tails again. The saved best also carried two reads
through `(volatile T *)` casts, which made every liveness-based family decline the whole function. The shape is
checked on a small function, never on game code."""
from decompnet.app import draft_repairs

SOURCE = '''s32 f(s32 c) {
  u8 *base;
  base = gTable;
  g(1, base);
  switch (c) {
    case 1:
      goto first;
    case 2:
      goto second;
    default:
      return 0;
  }
first:
  {
    base = gOther;
    g(2, base);
  }
after:
  h(base);
  if (w() == 0) {
    goto done;
  }
  k(base + 12);
  return 1;
second:
  if (*(volatile s32 *)&gStatus != 0) {
    goto after;
  }
done:
  return 2;
}
'''

FLAGS = ('-G8', '-mips3', '-mgp32', '-mfp32', '-O2')


def test_goto_run_copy_is_wired_into_the_repairs():
    assert 'goto_run_copy' in draft_repairs.AGENT_FAMILIES
    labels = {}
    out = draft_repairs.variants(SOURCE, 'f', 'mips', '', FLAGS, labels=labels, limit=400)
    copies = [v for v in out if labels.get(v) == 'goto_run_copy']
    assert copies
    # the run of `after:` sits in place of the goto of the arm that jumped in, which reads its own pointer
    assert any('base_w1' in v and v.count('h(base') == 2 and 'goto after' not in v for v in copies)


def test_a_volatile_cast_of_a_global_does_not_stop_the_flow_based_families():
    # `*(volatile s32 *)&gStatus` used to give local_flow no advice, so the web split above never ran
    assert '(volatile s32 *)' in SOURCE
    from decomprepair import local_flow
    assert local_flow.analyze(SOURCE, 'f') is not None
