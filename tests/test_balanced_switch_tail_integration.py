from decompnet.app.diff_gated_repairs import proposals

FAMILY = "balanced_switch_tail_layout"

SOURCE = """
void drive(void *actor, int state, s32 ok)
{
    f32 var_fs2;
    f32 raw_a;
    f32 raw_b;
    f32 raw_c;
    s32 mode;
    s32 index;
    mode = 2;
    index = mode * 2;
    if (ok || ((index = mode * 2, measure(actor)) > 0.1f))
    {
      update(actor, index);
    }
    raw_a = input(actor, 2, 3);
    var_fs2 = (raw_a * raw_a) * 1.5f;
    report(raw_a, var_fs2);
    raw_b = input(actor, 2, 3);
    var_fs2 = raw_b * raw_b;
    report(raw_b, var_fs2);
    raw_c = input(actor, 0, 1);
    var_fs2 = raw_c * 0.2f;
    report(raw_c, var_fs2);
    switch (state) {
      case 4:
        step(actor);
        *((f32 *) ((char *) actor + 20)) = 0.0f;
        *((u8 *) ((char *) actor + 36)) = 2;
        if (finish(actor) == 0) {
          flush(actor);
        }
        break;
      case 3:
        reset(actor);
        *((f32 *) ((char *) actor + 20)) = 0.0f;
        *((u8 *) ((char *) actor + 36)) = 2;
        if (finish(actor) == 0) {
          flush(actor);
        }
        break;
    }
}
"""


def identical(ops):
    return [(' ', op, op) for op in ops]


ROWS = [
    (' ', 'bnez $v0, .Lload', 'bnez $v0, .Lload'),
    ('r', 'sll $v0, $s0, 1', 'sll $s3, $s0, 1'),
    *identical(['jal measure', 'nop', 'bc1f .Ldone']),
    ('<', 'sll $v0, $s0, 1', ''),
    *identical(['addiu $a1, $zero, 2', 'jal input', 'addiu $a2, $zero, 3']),
    ('<', 'mov.s $f24, $f0', ''),
    *identical(['mul.s $f0, $f24, $f24']),
    ('<', 'mov.s $f22, $f24', ''),
    *identical(['move $a1, $zero', 'jal input', 'addiu $a2, $zero, 1']),
    ('<', 'mov.s $f24, $f0', ''),
    ('<', 'mov.s $f22, $f24', ''),
    *identical(['nop', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop', 'nop']),
    ('i', 'j .Ltail', 'j .Ltail2'),
    ('>', '', 'sw $zero, 20($s1)'),
    ('>', '', 'sb $v0, 36($s1)'),
    *identical(['move $a0, $s2', 'jal reset', 'move $a0, $s2', 'addiu $v0, $zero, 2',
                'sw $zero, 20($s1)', 'jal finish', 'sb $v0, 36($s1)']),
]


def test_deep_permuter_routes_the_complete_layout_witness():
    options = proposals(SOURCE, 'drive', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '', big_endian=True)
    [revised] = [text for label, text in options if label == FAMILY]
    assert revised.count('if (finish(actor)') == 1
    assert revised.count('var_fs2 = input(actor,') == 3
    assert revised.index('if (ok ||') < revised.index('index = mode * 2;')
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        other = proposals(SOURCE, 'drive', '', arch, compiler, ROWS, '', big_endian=True)
        assert all(label != FAMILY for label, _ in other)
