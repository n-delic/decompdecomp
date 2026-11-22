from decompnet.app import draft_repairs


def test_draft_repair_wont_split_a_counter_after_an_incoming_goto():
    source = """
extern void sink(int);
void f(int mode) {
    int counter;
    counter = 0;
    if (mode) goto entered;
    counter = 0;
entered:
    do {
        sink(counter);
        counter++;
    } while (counter < 4);
}
"""
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    assert all(labels.get(candidate) != 'reused_temp_split' for candidate in proposals)

    labels.clear()
    safe = source.replace('    counter = 0;\nentered:', 'entered:\n    counter = 0;')
    proposals = draft_repairs.variants(safe, 'f', 'mips', labels=labels)
    assert any(labels.get(candidate) == 'reused_temp_split' and 'sink(counter_b1);' in candidate
               for candidate in proposals)
