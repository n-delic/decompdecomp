from decompnet.app.diff_gated_repairs import proposals

FAMILY = "rejected_branch_default"

SOURCE = """
void classify(s32 low, s32 high, s32 final)
{
    s32 var_s0;
    f32 value;
    var_s0 = 1;
    if (sample() <= 0.1f)
    {
        value = 0.0f;
        store(value);
    }
    else
    {
        observe();
        var_s0 = 1;
        if (!(low & high))
        {
            observe();
            var_s0 = 1;
            if (final)
            {
                var_s0 = 2;
            }
        }
    }
    use(var_s0);
}
"""

ROWS = [
    (' ', 'bc1f .Ltested', 'bc1f .Ltested'),
    (' ', 'addiu $s0, $zero, 1', 'addiu $s0, $zero, 1'),
    (' ', 'and $v0, $v0, $v1', 'and $v0, $v0, $v1'),
    ('i', 'bnez $v0, .Ldone', 'bnez $v0, .Lprior'),
    ('o', 'addiu $s0, $zero, 1', 'nop'),
    (' ', 'and $v0, $v0, $v1', 'and $v0, $v0, $v1'),
    ('i', 'bnez $v0, .Ldone', 'bnez $v0, .Lprior'),
    ('<', 'addiu $s0, $zero, 1', ''),
]


def test_deep_permuter_routes_the_measured_rejected_branch_default():
    options = proposals(SOURCE, 'classify', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [revised] = [text for label, text in options if label == FAMILY]
    assert 'value = 0.0f;\n        var_s0 = 1;' in revised
    assert 'if (low & high) {\n          var_s0 = 1;' in revised
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'classify', '', arch, compiler, ROWS, ''))
