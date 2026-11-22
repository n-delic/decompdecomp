"""The shared switch-tail proposal reaches the Deep Permuter."""

from decompnet.app.draft_repairs import variants

SOURCE = """\
void f(int state, int early, int late) {
    float rate;
    switch (state) {
    case 3:
        if (early) {
            rate = 0.2f;
            goto shared;
        }
        if (late) {
            rate = 0.0f;
        shared:
            start(rate);
            finish();
        }
        goto done;
    }
done:
    report();
}
"""


def test_shared_case_tail_can_relocate_through_shared_proposals():
    labels = {}
    proposals = variants(SOURCE, 'f', 'mips', labels=labels)
    [moved] = [candidate for candidate in proposals if labels[candidate] == 'goto_relocated_case_tail']
    assert moved.index('start(rate);') < moved.index('if (late)')
    assert moved.index('if (late)') < moved.index('goto shared;')
