from decompnet.app import draft_repairs


def test_draft_repairs_offer_the_guarded_pointer_copy():
    source = """
extern int amount;
void f(void **record) {
    void *destination;
    void *entry;
    entry = record[0];
    if (entry != NULL) {
        amount += 1;
        *(int *)((char *)entry + 8) = amount;
        destination = record[1];
        *(int *)destination = 0;
    }
}
"""
    labels = {}
    proposals = draft_repairs.variants(source, 'f', 'mips', labels=labels)
    assert any(labels.get(candidate) == 'guarded_pointer_copy'
               and 'destination = entry;' in candidate
               and '(char *)destination + 8' in candidate for candidate in proposals)
