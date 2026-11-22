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


def test_weighted_pack_reaches_the_shared_repair_lane():
    identity = SOURCE.replace('(((0xff000000 | high) | low) | shade)',
                              '(((high | ((~low & 0xff000000) | low)) | shade))')
    for source in (SOURCE, identity):
        labels = {}
        draft_repairs.variants(source, 'f', 'mips', header='', labels=labels, limit=None)
        assert 'weighted_or_pack' in draft_repairs.AGENT_FAMILIES
        assert any(family == 'weighted_or_pack' and 'high |= __repair_or_part' in text
                   for text, family in labels.items())
    labels = {}
    draft_repairs.variants(identity, 'f', 'mips', header='#define low read()', labels=labels, limit=None)
    assert 'weighted_or_pack' not in labels.values()
