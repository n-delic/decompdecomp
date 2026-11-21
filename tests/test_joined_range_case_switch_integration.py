"""The range-switch repair appears in the shared permuter proposal path."""

from decompnet.app.draft_repairs import variants

SOURCE = """\
void f(int state) {
    int selected;
    selected = state;
    if (selected < 4) {
        if (selected < 2) {
            if (selected != 1) {
            } else {
                goto joined;
            }
        } else {
            middle();
        }
    } else if (selected == 4) {
    joined:
        step();
    }
}
"""


def test_joined_range_case_switch_is_available_to_the_deep_permuter():
    labels = {}
    candidates = variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    lifted = [candidate for candidate in candidates if labels[candidate] == 'joined_range_case_switch']
    assert any('case 1:' in candidate and 'case 4:' in candidate and 'case 2:' in candidate
               and 'case 3:' in candidate for candidate in lifted)
