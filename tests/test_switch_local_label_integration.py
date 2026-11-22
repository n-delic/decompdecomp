from decompnet.app import draft_repairs

LOCAL_TAIL = '''
void f(int state, int ready) {
    switch (ready) {
    case 0:
        first();
        break;
    case 3:
        second();
        break;
    }
    switch (state) {
    case 2:
        finish();
        break;
    case 1:
        if (ready) {
            if (retry()) goto store_state;
            send();
        } else {
store_state:
            set_state();
        }
        break;
    }
}
'''



def test_draft_repairs_offer_switch_order_with_private_labels():
    labels = {}
    children = draft_repairs.variants(LOCAL_TAIL, 'f', 'mips', '', '-O2', labels, limit=None)
    choices = [text for text in children if labels[text] == 'switch_case_local_label_order']
    assert choices
    state_switch = choices[0].split('switch (state)', 1)[1]
    assert state_switch.index('case 1:') < state_switch.index('case 2:')
    assert 'case 3:' not in state_switch
