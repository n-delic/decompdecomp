from decompnet.app import draft_repairs

SOURCE = '''
void sample(s32 argument) {
    s32 value;
    if (!ready) {
        call(argument & 0xff);
    }
    argument = source_value;
    value = argument;
    sink = value;
}
'''


def test_parameter_split_is_offered_and_respects_header_macros():
    labels = {}
    options = draft_repairs.variants(SOURCE, 'sample', 'mips', labels=labels, limit=None)
    assert any(labels.get(option) == 'split_integer_lifetime'
               and '__repair_argument_local = source_value;' in option for option in options)
    labels = {}
    options = draft_repairs.variants(SOURCE, 'sample', 'mips', labels=labels, limit=None,
                                     header='#define call(x) #x')
    assert not any(labels.get(option) == 'split_integer_lifetime'
                   and '__repair_argument_local' in option for option in options)
