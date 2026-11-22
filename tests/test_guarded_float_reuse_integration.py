from decompnet.app import draft_repairs


def test_mips_draft_repairs_reuse_float_with_guarded_label():
    source = '''
void f(int test) {
    f32 var_fs3;
    f32 temp_fs3;
    var_fs3 = first();
    use(var_fs3);
    temp_fs3 = second();
    if (test) goto finish;
    step();
finish:
    use(temp_fs3);
}
'''
    labels = {}
    choices = draft_repairs.variants(source, 'f', 'mips', '', '-O2', labels, limit=None)
    [candidate] = [text for text in choices if labels[text] == 'mips_float_local_reuse']
    assert 'temp_fs3' not in candidate
    assert 'var_fs3 = second();' in candidate
    assert candidate.count('use(var_fs3);') == 2
