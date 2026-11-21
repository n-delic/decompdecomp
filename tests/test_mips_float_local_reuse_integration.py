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


def test_paired_accumulator_reuse_is_a_mips_permuter_proposal():
    source = """\
void f(void) {
    f32 var_s5;
    f32 var_s6;
    f32 temp_s5_2;
    f32 temp_s6_2;
    temp_s5_2 = first();
    temp_s6_2 = second();
    var_s5 = temp_s5_2 + adjust();
    var_s6 = temp_s6_2 + adjust();
    emit(var_s5, var_s6);
}
"""
    labels = {}
    proposals = variants(source, 'f', 'mips', labels=labels)
    paired = [text for text in proposals if labels[text] == 'mips_float_accumulator_reuse'
              and 'temp_s5_2' not in text and 'temp_s6_2' not in text]
    assert len(paired) == 1
    assert 'var_s5 = var_s5 + adjust();' in paired[0]
    assert 'var_s6 = var_s6 + adjust();' in paired[0]
