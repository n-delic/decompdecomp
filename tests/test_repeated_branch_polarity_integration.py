from decompnet.app import generation_calls


def test_worker_rewrites_repeated_flag_branches_together():
    source = """void f(unsigned flags, int output) {
        if (!(flags & 4)) {
            keep_first();
        } else if (output) {
            set_first();
        }
        if (!(flags & 4)) {
            keep_second();
        } else {
            set_second();
        }
    }"""
    proposals, = generation_calls.library([
        ('decomprepair.branch_polarity_repairs.variants', (source, 'f')),
    ])
    family, text = proposals[0]
    assert family == 'branch_polarity'
    assert text.count('if (flags & 4)') == 2
    assert text.index('if (output)') < text.index('keep_first()')
