"""The MIPS proposal path preserves and uses register-local hints."""

from decompnet.app.draft_repairs import variants

SOURCE = """\
void f(void) {
    f32 var_fs3;
    f32 temp_fs3;
    {
        var_fs3 = first();
        send(var_fs3);
    }
    {
        temp_fs3 = second();
        send(temp_fs3);
    }
}
"""


def test_saved_float_local_reuse_is_a_mips_permuter_proposal():
    labels = {}
    proposals = variants(SOURCE, 'f', 'mips', labels=labels)
    [reused] = [text for text in proposals if labels[text] == 'mips_float_local_reuse']
    assert 'temp_fs3' not in reused
    assert reused.count('send(var_fs3)') == 2
    labels = {}
    variants(SOURCE, 'f', 'ppc', labels=labels)
    assert 'mips_float_local_reuse' not in labels.values()
