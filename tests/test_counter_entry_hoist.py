from decompnet.app import draft_repairs


def test_draft_repairs_offer_a_plain_counter_initialization_before_the_guard():
    source = """
extern void sink(int);
void f(int size) {
    int index;
    index = 7;
    if (size > 0) {
        index = 0;
        do {
            sink(index);
            index++;
        } while (index < size);
    }
}
"""
    labels = {}
    result = draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=190)
    assert any(labels.get(candidate) == 'loop_counter_init_hoist'
               and '    index = 0;\n    if (size > 0)' in candidate
               and '__repair_loop_weight' not in candidate for candidate in result)
