"""The two families found by a 110 -> 0 menu function reach the draft pass, the beam and the local lane through the registry.

join_tail_arm_inline (a join's store written in each arm, the arm's temporaries folded into it) and local_width_group
(copy-connected word locals narrowed together) declare REPAIR in decomp-repair, so the app has nothing to wire; this keeps
that true and keeps the two-step chain (110 -> 20 -> 0 on the real function) reproducible on a made-up source of its shape."""
from decompnet.app import draft_repairs, library_registry
from decomprepair import registry

SOURCE = '''extern s32 gTable[];
extern s32 D_a;
extern u8 gRows[];
extern void *gState;
void f(void *p, s32 i, s32 start, s32 count)
{
  void *row;
  s32 value;
  s32 low;
  s32 sel;
  u32 cur;
  sel = start;
  cur = sel;
  while ((u8)cur < count) {
    if (gRows[(u8)cur * 4] & 0x80) {
      sel = cur;
      break;
    }
    cur++;
  }
  if (i != 0) {
    row = *(void **)((s8 *)p + 8);
    value = gTable[i];
    low = *(u8 *)((s8 *)gState + 0x30);
  } else {
    row = *(void **)((s8 *)p + 12);
    value = D_a;
    low = *(u8 *)((s8 *)row + 0x13);
  }
  *(s32 *)((s8 *)row + 0x10) = (value & ~0xFF) | low;
  *(u8 *)((s8 *)p + 4) = (u8)sel;
}
'''


def test_the_registry_declares_both_families_in_every_lane():
    for module, family in (('join_tail_arm_inline', 'join_tail_arm_inline'), ('local_width_group', 'local_width_group')):
        repair = registry.get(module)
        assert repair is not None and repair.families == (family,) and repair.lanes == registry.LANES
        assert family in draft_repairs.AGENT_FAMILIES


def test_the_draft_pass_offers_each_family_and_the_chain_reaches_both_edits():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert {'join_tail_arm_inline', 'local_width_group'} <= set(labels.values())
    folded = next(text for text, family in labels.items() if family == 'join_tail_arm_inline')
    assert 'value' not in folded and '(D_a & ~0xFF)' in folded
    narrowed = draft_repairs.variants(folded, 'f', 'mips', labels=(second := {}), limit=None)
    assert narrowed and any(family == 'local_width_group' for family in second.values())
    local = library_registry.local_calls(library_registry.draft_context(SOURCE, 'f', '', 'mips', '', ()))
    assert {'join_tail_arm_inline', 'local_width_group'} <= {call[1][0] for call in local}
