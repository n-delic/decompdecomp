from decompnet.app import draft_repairs

SOURCE = '''void f(u32 shade) {
    u32 high = shade << 16; u32 low = shade << 8;
    do { out = (((0xff000000 | high) | low) | shade); } while (0);
}'''


def test_unsigned_or_identity_is_offered_and_header_macros_are_respected():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', header='', labels=labels, limit=None)
    assert any(family == 'or_chain_order' and '~low & 0xff000000' in text
               for text, family in labels.items())
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', header='#define low read()', labels=labels, limit=None)
    assert not any(family == 'or_chain_order' and '~low & 0xff000000' in text
                   for text, family in labels.items())
