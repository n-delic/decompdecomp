from decompnet.app.diff_gated_repairs import proposals

FAMILY = "staged_epsilon_predicate_order"

SOURCE = '''
void f(f32 magnitude) {
    int var_v1_3;
    int var_v0_4;
    var_v1_3 = 1;
    if (!(magnitude <= 0.0002f)) {
        var_v1_3 = 0;
    }
    var_v0_4 = 1;
    if (!(magnitude >= (-0.0002f))) {
        var_v0_4 = 0;
    }
    if (((var_v1_3 ^ 1) | (var_v0_4 ^ 1)) == 0) {
        ready();
    }
}
'''

STAGED_SOURCE = SOURCE.replace(
    '    int var_v0_4;', '    int var_v0_4;\n    int held;',
).replace(
    '    var_v0_4 = 1;', '    held = var_v1_3 ^ 1;\n    var_v0_4 = 1;',
).replace(
    '(var_v1_3 ^ 1) | (var_v0_4 ^ 1)', '(var_v0_4 ^ 1) | held',
)
STAGED_ROWS = [
    'lui $at, 0x3951',
    'ori $at, $at, 0xb717',
    'mtc1 $at, $f0',
    'nop',
    'c.le.s $f26, $f0',
    'nop',
    'nop',
    'bc1t .Lfirst',
    'addiu $v1, $zero, 1',
    'move $v1, $zero',
    'lui $at, 0xb951',
    'ori $at, $at, 0xb717',
    'mtc1 $at, $f0',
    'nop',
    'c.le.s $f0, $f26',
    'addiu $v0, $zero, 1',
    'nop',
    'bc1t .Lsecond',
    'xori $v1, $v1, 1',
    'move $v0, $zero',
    'xori $v0, $v0, 1',
]
STAGED_DIFF = [(' ', op, op) for op in STAGED_ROWS] + [('r', 'or $v0, $v1, $v0', 'or $v0, $v0, $v1')]


def test_deep_permuter_orders_already_staged_predicates_for_kmc():
    [candidate] = [text for label, text in proposals(STAGED_SOURCE, 'f', '', 'mips',
                                                    'gcc-2.7.2-kmc', STAGED_DIFF, '') if label == FAMILY]
    assert 'held | (var_v0_4 ^ 1)' in candidate
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(STAGED_SOURCE, 'f', '', arch, compiler, STAGED_DIFF, ''))
