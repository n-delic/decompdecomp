from decompnet.app.diff_gated_repairs import proposals

FAMILY = "clamp_return_local_reuse"

HEADER = 'f32 absolute(f32);\n'
FUNCTION = """void match_demo(void* p, int flags, int mask, f32 input) {
    f32 sample;
    f32 bound;
    if ((flags & mask) && (absolute(input) < 0.01f)) {
        *((f32*)(((char*)p)+0x18)) = 0.0f;
    }
    sample = *((f32*)(((char*)p)+0x18));
    bound = -0.25f;
}
"""
ROWS = [
    (' ', 'jal absolute', 'jal absolute'),
    (' ', 'neg.s $f12, $f0', 'neg.s $f12, $f0'),
    (' ', 'c.lt.s $f0, $f2', 'c.lt.s $f0, $f2'),
    (' ', 'bc1tl .Lnext', 'bc1tl .Lnext'),
    (' ', 'sw $zero, 0x18($s0)', 'sw $zero, 0x18($s0)'),
    ('<', 'lwc1 $f0, 0x18($s0)', ''),
    (' ', 'lui $at, 0xbe80', 'lui $at, 0xbe80'),
    (' ', 'ori $at, $at, 0x0', 'ori $at, $at, 0x0'),
    ('i', 'mtc1 $at, $f2', 'mtc1 $at, $f0'),
    ('|', 'nop', 'lwc1 $f2, 0x18($s0)'),
    ('i', 'c.lt.s $f0, $f2', 'c.lt.s $f2, $f0'),
]


def test_deep_permuter_uses_the_witnessed_probe_local_for_kmc():
    [candidate] = [text for label, text in proposals(FUNCTION, 'match_demo', HEADER, 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert candidate == FUNCTION.replace('absolute(input)', '(sample = absolute(input))')
    for arch, compiler in [('arm', 'gcc-2.7.2-kmc'), ('mips', 'gcc-egcs'), ('mips', 'ido-5.3')]:
        assert all(label != FAMILY for label, _ in proposals(FUNCTION, 'match_demo', HEADER, arch, compiler, ROWS, ''))
