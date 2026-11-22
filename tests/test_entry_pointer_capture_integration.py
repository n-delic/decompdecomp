from decompnet.app import draft_repairs

SOURCE = '''
extern s8 state;
inline static s8 read_slot(char *obj, int offset) {
    return *((s8 *) (obj + offset));
}
void f(void *arg) {
    struct { void *object; } frame;
    void *actor;
    int slot;
    do {
        frame.object = *((void **) (((char *) arg) + 0x20));
        actor = *((void **) (((char *) arg) + 0x6C));
        slot = read_slot((char *) actor, 0x182);
    } while (0);
    if (state == 0) {
        dispatch(frame.object, actor, slot);
    }
}
'''

ROWS = [
    'lw $t0, 0x20($s2)',
    'lw $s1, 0x6c($s2)',
    'lui $v0, %hi(state)',
    'lb $v0, %lo(state)($v0)',
    'sw $t0, 0x64($sp)',
    'lb $s4, 0x182($s1)',
]


def asm(rows):
    return '\n'.join(f'/* {index*4:06X} {0x80000000+index*4:08X} 00000000 */ {op}'
                     for index, op in enumerate(rows))



def test_mips_draft_repairs_offer_staged_entry_pointer():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', asm(ROWS), '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'entry_pointer_capture']
    assert candidate.index('entry_mode = state;') < candidate.index('frame.object = entry_road;')
    assert 's32 entry_mode;' in candidate
