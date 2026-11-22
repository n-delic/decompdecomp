from decompnet.app import draft_repairs

SOURCE = '''
void f(void) {
    unsigned short state;
    state = get_mode();
    if (((state >= 0) && (state >= 2)) && (state < 4)) {
        if (ready()) {
            send(state);
        }
    }
    finish();
}
'''



def test_draft_repairs_offer_bounded_mode_switch():
    labels = {}
    children = draft_repairs.variants(SOURCE, 'f', 'mips', '', '-O2', labels, limit=None)
    [candidate] = [text for text in children if labels[text] == 'bounded_mode_switch']
    assert 'switch (state)' in candidate
    assert 'case 0: case 1: break;' in candidate
    assert 'case 2: case 3: {' in candidate
