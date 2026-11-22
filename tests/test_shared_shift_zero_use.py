"""decomprepair 0.5.591: a shift added twice (chain plus a known-zero use) reaches the draft repairs, the deep pass, the local lane."""
from decompnet.app import draft_repairs, local_experiments

FLAGS = ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']
HEADER = 'typedef int s32;\ntypedef unsigned int u32;\ntypedef unsigned short u16;\ntypedef unsigned char u8;\n'
SOURCE = HEADER + '''s32 alloc();
void f(u8 *h) {
  s32 v;
  alloc(((u32) (((*(u16 *) (h + 0x14)) * 8) + (v = (*(u16 *) (h + 0xC)) * 4))) + ((*(u16 *) (h + 0x1C)) + v), 0x2D);
}
'''
NEW = ('shared_shift_chain', 'shared_shift_zero_use', 'shared_shift_combination')


def test_families_are_known_to_the_agents_and_the_local_lane():
    for family in NEW:
        assert family in draft_repairs.AGENT_FAMILIES
        assert family in local_experiments.FAMILIES


def test_the_combined_child_is_offered_the_way_the_deep_pass_and_local_lane_ask():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', '', FLAGS, labels, limit=None, header=HEADER)
    combined = [t for t in texts if labels.get(t) == 'shared_shift_combination']
    assert combined and 'v + (*(u16 *) (h + 0x1C)) + (v & 3), 0x2D);' in combined[0]
    assert '(v = (*(u16 *) (h + 0xC)) * 4) + v + ' in combined[0]


def test_the_goto_family_then_the_shift_family_chain_to_the_expected_text():
    """Two released families in sequence: the later one still reads the shape the earlier one leaves."""
    from decomprepair import goto_structure_repairs, shared_shift_zero_use
    source = HEADER + '''s32 alloc();
void use();
void f(u8 *h, s32 *arg0) {
    s32 v;
    s32 held;
    held = *(s32 *)((char *)arg0 + 0x34);
    if (held != 0) {
        goto L;
    }
    use(1);
    if (arg0 != 0) {
        held = 5;
L:
        alloc(((u32) (((*(u16 *) (h + 0x14)) * 8) + (v = (*(u16 *) (h + 0xC)) * 4))) + ((*(u16 *) (h + 0x1C)) + v), 0x2D);
    }
}
'''
    stage = dict(goto_structure_repairs.variants(source, 'f')).get('goto_nested_entry_else')
    assert stage and 'entry_tmp' in stage
    final = dict(shared_shift_zero_use.variants(stage, 'f'))['shared_shift_combination']
    assert '+ (v & 3), 0x2D);' in final and '(u32)' not in final
