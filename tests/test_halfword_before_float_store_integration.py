from decompnet.app.diff_gated_repairs import proposals

FAMILY = "halfword_before_float_store"

SOURCE = """
extern f32 scale;
int adjust(void *actor)
{
    scale = 0.6f;
    if ((*((s16 *) (((char *) (*((void **) (((char *) actor) + 20)))) + 16))) < 7)
        return 1;
    return 0;
}
"""

ROWS = [
    ('o', 'lw $v0, 20($s1)', ''),
    (' ', 'lui $at, 0x3f19', 'lui $at, 0x3f19'),
    (' ', 'ori $at, $at, 0x999a', 'ori $at, $at, 0x999a'),
    (' ', 'mtc1 $at, $f0', 'mtc1 $at, $f0'),
    ('o', 'lh $v0, 16($v0)', ''),
    (' ', 'lui $at, %hi(scale)', 'lui $at, %hi(scale)'),
    (' ', 'swc1 $f0, %lo(scale)($at)', 'swc1 $f0, %lo(scale)($at)'),
    ('o', '', 'lw $v0, 20($s1)'),
    ('o', '', 'lh $v0, 16($v0)'),
    (' ', 'slti $v0, $v0, 7', 'slti $v0, $v0, 7'),
]


def test_deep_permuter_routes_signed_field_order_for_kmc():
    options = proposals(SOURCE, 'adjust', '', 'mips', 'gcc-2.7.2-kmc', ROWS, '')
    [revised] = [text for label, text in options if label == FAMILY]
    assert revised.index('repair_signed_field =') < revised.index('scale = 0.6f;')
    assert 'if (repair_signed_field < 7)' in revised
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'ido-5.3'), ('mips', 'gcc-4.4.0-n64')]:
        assert all(label != FAMILY for label, _ in proposals(SOURCE, 'adjust', '', arch, compiler, ROWS, ''))
