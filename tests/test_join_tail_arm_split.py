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


CHAIN = '''void f(s32 mode) {
  s32 *node;
  s32 *state;
  s32 idle;
  node = g_first;
  if (mode == 0x80) {
    node = g_second;
    state = g_state;
    idle = g_idle;
    goto emit;
  }
  if (mode == 0x81) {
    node = g_second;
    state = g_state;
    do {
      idle = g_other;
emit:
      node[4] = ((idle & -256) | state[12]) & -256 | state[7];
    } while (0);
  } else {
    node = g_second;
    state = g_state;
    node[4] = (g_idle & -256) | state[12];
  }
  g_done = 1;
}
'''


def test_goto_chain_tail_split_reaches_the_drafts_with_each_all_but_one_rename():
    """decomp-repair 0.5.602: `goto L` into the next if's tail as an else-if chain carrying the tail. From a KMC N64
    function (gcc 2.7.2) at 80, the child pointer left shared and every other name split per arm was 0 (judge-worker
    on that function; this checks the route with a synthetic one)."""
    assert 'goto_chain_tail_split' in draft_repairs.AGENT_FAMILIES
    out = draft_repairs.variants(CHAIN, 'f', 'mips', '', ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2'), limit=400)
    chains = [v for v in out if '} else if (mode == 0x81)' in v and 'goto emit' not in v]
    assert chains
    assert any('state_a1' in v and 'node_a1' not in v for v in chains), 'the shared node was not offered'


def test_join_tail_arm_split_is_wired_into_the_repairs():
    for family in ('arm_private_rename', 'join_tail_arm_split', 'goto_body_copy', 'phase_union_unshare'):
        assert family in draft_repairs.AGENT_FAMILIES
    out = draft_repairs.variants(SOURCE, 'f', 'mips', '', ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2'), limit=400)
    assert any('p_a1[1] = v_a1;' in v and 'p_a2[1] = v_a2;' in v for v in out)


DEFAULT = '''void f(u8 *a, u16 *out) {
  u16 v;
  v = 0xA0;
  if (a[8] != 0xFF)
  {
    v = *(u16 *)(a + 4);
  }
  *out = v;
}
'''


def test_default_into_then_reaches_the_drafts():
    """decomp-repair 0.5.605: the default as the then arm of the inverted test. From a KMC N64 function (gcc 2.7.2) it
    was 95 -> 80 on the saved best and 15 -> 0 after goto_chain_tail_split (isolated judge); this checks the route."""
    assert 'default_into_then' in draft_repairs.AGENT_FAMILIES
    out = draft_repairs.variants(DEFAULT, 'f', 'mips', '', ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2'), limit=400)
    assert any('if (a[8] == 0xFF)' in v and 'else' in v for v in out)
