from decompnet.app import draft_repairs


def test_repeated_unsigned_offsets_are_offered_as_one_inline_helper():
    source = """unsigned f(unsigned arg) {
        unsigned words[2];
        words[0] = arg;
        use(arg + (((words[0] >> 6) * 40) + 256));
        return arg + (((words[0] >> 6) * 40) + 256);
    }"""
    family = 'repeated_scaled_offset_helper'
    assert family in draft_repairs.AGENT_FAMILIES
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', flags=['-O2'], labels=labels, limit=None)
    candidate, = [text for text in candidates if labels.get(text) == family]
    assert candidate.count('repair_scaled_offset(words[0])') == 2
