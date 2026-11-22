from decompnet.app import draft_repairs

SOURCE = '''
void f(char *record) {
    u8 earlier_a;
    u8 earlier_b;
    u8 earlier_c;
    u8 earlier_d;
    u8 earlier_e;
    u8 earlier_f;
    u8 earlier_g;
    u8 earlier_h;
    u8 mode;
    mode = 1;
    if (poll()) {
        mode = 2;
    }
    if (mode != (*((u8 *) (record + 3)))) {
        *((u8 *) (record + 3)) = mode;
    }
}
'''



def test_draft_repairs_offer_small_constant_mode_after_the_old_cap():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', '', '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'narrow_constant_mode']
    assert candidate == SOURCE.replace('u8 mode;', 's32 mode;')
