"""decomprepair 0.5.333's three families reach the repairs the deep pass and autopilot try.

From an N64 actor-update function (GCC 2.7.2 KMC, `-G0 -mips3 -mgp32 -mfp32 -O2`), each scored with the judge-worker on the
function's own before-state: `arm_pointer_split` 275 to 245, `param_address_local` 60 to exact, `slot_helper` 120 to 60
(no permuter run)."""
from decompnet.app import draft_repairs

FLAGS = ('-G0', '-mips3', '-mgp32', '-mfp32', '-O2')

JOINED = '''void f(void *a, s32 i) {
  f32 v;
  void *p;
  void *q;
  if (i == 5) {
    q = *(void **) a;
    p = *(void **) q;
    v = *(f32 *) (((char *) p) + 0x14) + 1.0f;
  } else {
    p = *(void **) ((char *) a + 4);
    v = *(f32 *) (((char *) p) + 0x2C) * 0.5f;
  }
  *(f32 *) (((char *) p) + 0x14) = v;
  g(a);
}
'''

ADDRESS = '''void f(void *arg0) {
  char sp18[12];
  char sp28[48];
  g(arg0);
  do { arg0 = sp18; } while (0);
  h(sp28, arg0);
  k(arg0, 4);
}
'''

SLOT = '''void f(void *arg0, s32 idx) {
  void **slot;
  slot = (idx * 4) + gTable;
  call(arg0 + 0x24, *slot, 1.0f, 10.0f);
  call2(arg0, *slot);
}
'''


def _variants(source):
    return draft_repairs.variants(source, 'f', 'mips', '', FLAGS, limit=400)


def test_the_families_are_wired_into_the_repairs():
    for family in ('arm_pointer_split', 'param_address_local', 'slot_helper'):
        assert family in draft_repairs.AGENT_FAMILIES


def test_each_family_reaches_the_variants_the_deep_pass_scores():
    assert any('p_arm1' in v and 'p_arm2' in v for v in _variants(JOINED))
    assert any('arg0_addr = sp18;' in v and 'k(arg0_addr, 4);' in v for v in _variants(ADDRESS))
    assert any('__repair_slot(gTable, idx)' in v for v in _variants(SLOT))
