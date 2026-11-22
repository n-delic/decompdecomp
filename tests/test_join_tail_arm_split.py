"""decomp-repair 0.5.571's `join_tail_arm_split` families reach the repairs the deep pass and autopilot try.

From a KMC-compiled N64 command-emission function (gcc 2.7.2): m2c's cursor and value shared by the arms of an emit
were global pseudos; the tail copied into each arm with names of its own was the step behind 810 -> 0 (the beam over
only these families, scored with the judge-worker; no permuter run)."""
from decompnet.app import draft_repairs

SOURCE = '''void f(s32 i) {
  s32 *p;
  s32 v;
  if (i & 3) {
    p = g_cursor;
    g_cursor = p + 2;
    p[0] = 0xDE000000;
    v = 5;
  } else {
    p = g_cursor;
    g_cursor = p + 2;
    p[0] = 0xDE000000;
    v = 7;
  }
  p[1] = v;
  g_done = 1;
}
'''


def test_join_tail_arm_split_is_wired_into_the_repairs():
    for family in ('arm_private_rename', 'join_tail_arm_split', 'goto_body_copy', 'phase_union_unshare'):
        assert family in draft_repairs.AGENT_FAMILIES
    out = draft_repairs.variants(SOURCE, 'f', 'mips', '', ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2'), limit=400)
    assert any('p_a1[1] = v_a1;' in v and 'p_a2[1] = v_a2;' in v for v in out)
