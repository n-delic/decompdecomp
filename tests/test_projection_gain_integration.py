from decompnet.app.diff_gated_repairs import proposals

FAMILY = 'saved_projection_gain_reuse'

SOURCE = """float sine(float);
float cosine(float);
void reduce(char *record, float angle, int mode)
{
    float gain;
    float x;
    float y;
    if (mode) {
        *((f32 *) (((char *) record) + 0x18)) = 0.5f;
        x = sine(angle) * 0.5f;
        y = cosine(angle) * 0.5f;
        use(x, y);
    } else {
        gain = 0.5f;
        *((f32 *) (((char *) record) + 0x18)) = gain;
        x = gain * sine(angle);
        gain = gain * cosine(angle);
        use(x, gain);
    }
}
"""


def sequence(register, *, donor=False):
    first = f'mul.s $f0, {register}, $f0' if donor else f'mul.s $f0, $f0, {register}'
    second = f'mul.s {register}, {register}, $f0' if donor else first
    return [
        'lui $at, 0x3f00', 'ori $at, $at, 0', f'mtc1 $at, {register}', 'nop',
        f'swc1 {register}, 0x18($s0)', 'jal sine', 'nop', first, 'jal cosine', 'nop', second,
        'jal use',
    ]


DEMAND = [(' ' if left == right else 'r', left, right)
          for left, right in zip(sequence('$f26'), sequence('$f20'))]
DONOR = [(' ', op, op) for op in sequence('$f26', donor=True)]
ROWS = DEMAND + DONOR


def test_deep_permuter_reuses_the_witnessed_constant_projection_holder_for_kmc():
    [candidate] = [text for label, text in proposals(SOURCE, 'reduce', '', 'mips',
                                                    'gcc-2.7.2-kmc', ROWS, '') if label == FAMILY]
    assert '        gain = 0.5f;\n        *((f32 *) (((char *) record) + 0x18)) = gain;' in candidate
    assert '        x = sine(angle) * gain;\n        y = cosine(angle) * gain;' in candidate
    assert FAMILY not in {label for label, _ in proposals(SOURCE, 'reduce', '', 'mips', 'ido-7.1', ROWS, '')}
