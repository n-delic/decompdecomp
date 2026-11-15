"""Synthetic instruction fixtures; no extracted game source or bytes."""
import pytest

from decompnet.app.parameter_transfers import destination_signature, scale_changes, variants


def pair(big=True):
    def fn(divisor, shift, vram):
        magic = ((1 << (32 + shift)) + divisor - 1) // divisor
        words = [0x28820000 | divisor, 0x2484_0000 | (-divisor & 65535),
                 0x3c080000 | (magic >> 16), 0, 0x35080000 | (magic & 65535),
                 0x00880018, 0x00001010, 0x00021003 | shift << 6,
                 0x08000000 | (vram + 40) >> 2 & 0x3ffffff, 0, 0x03e00008, 0]
        return dict(target=b''.join(w.to_bytes(4, 'big' if big else 'little') for w in words),
                    relocs='[]', vram=vram)
    return fn(50, 4, 0x80001000), fn(500, 5, 0x80002000)


SOURCE = '''void destination(void *object, short *output) {
    int buffer[50]; /* / 50 */ char *text = "50";
    if (x < 50) { x = (x - 50) / 50; }
    a = a / 50; b = b / 50; c = c / 50; d = d / 50; e = e / 50;
    object = object + 50;
}'''


@pytest.mark.parametrize('big', [True, False])
def test_one_candidate_changes_eight_related_literals_and_preserves_source_shape(big):
    a, b = pair(big)
    assert scale_changes(a, b, big) == [(50, 500)]
    result = variants(SOURCE, a, b, big)
    assert len(result) == 1
    assert result[0].count('500') == 8
    assert 'buffer[50]' in result[0] and 'object + 50' in result[0]
    assert '/* / 50 */' in result[0] and '"50"' in result[0]
    assert result[0].count('\n') == SOURCE.count('\n')


@pytest.mark.parametrize('index,word', [(5, 0x00a80018), (4, 0x35081234), (7, 0x00021183), (8, 0x08003000)])
def test_registers_wrong_reciprocals_shifts_and_external_jumps_reject_transfer(index, word):
    a, b = pair()
    b['target'] = b['target'][:index*4] + word.to_bytes(4, 'big') + b['target'][(index+1)*4:]
    assert scale_changes(a, b, True) == []


def test_incomplete_evidence_and_unrelated_numbers_are_not_a_parameter():
    a, b = pair()
    assert variants('return 50 + 50;', a, b, True) == []
    a['relocs'] = '[[8,5,0]]'
    assert variants(SOURCE, a, b, True) == []


def test_signature_preserves_parameter_names_and_entire_body():
    donor = 'void destination(void *object, void *output) {\n int x; x=1;\n}'
    own = 'void destination(void *arg0, short *arg1) { return; }'
    result = destination_signature(donor, 'destination', own)
    assert result == 'void destination(void *object, short *output) {\n int x; x=1;\n}'
    assert destination_signature(donor, 'destination', 'not a definition') == donor
    assert destination_signature(donor, 'destination', 'void destination(int (*callback)(int)) {}') == donor


def test_multiline_signature_keeps_line_breaks_and_donor_names():
    donor = 'void destination(void *object,\n    void *output) {\n return;\n}'
    own = 'void destination(void *a, short *b) {}'
    assert destination_signature(donor, 'destination', own) == donor.replace('void *output', 'short *output')
