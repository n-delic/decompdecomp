from decompnet.app import draft_repairs


def test_guard_temp_sink_is_offered_after_an_initialized_declaration():
    source = '''
int f(int input) {
    int sum;
    sum = (unsigned char)input + 16;
    if (input != 255) {
        int result = input;
        if (sum >= 0) {
            result += 16;
            if (sum >= 256) result = 255;
        } else {
            result = 0;
        }
        return result;
    }
    return 0;
}
'''
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    assert any(labels.get(candidate) == 'guard_temp_sink'
               and 'int result = input;\n        sum = (unsigned char)input + 16;' in candidate
               for candidate in proposals)
