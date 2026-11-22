from decompnet.app import draft_repairs


def test_app_offers_mask_then_multiply_in_the_unsigned_destination():
    source = """unsigned f(unsigned arg) {
        unsigned offset;
        offset = (arg & 0xff) * 134;
        return offset;
    }"""
    family = 'masked_product_stage'
    assert family in draft_repairs.AGENT_FAMILIES
    labels = {}
    candidates = draft_repairs.variants(source, 'f', 'mips', flags=['-O2'], labels=labels, limit=None)
    assert any(labels.get(text) == family and '{ offset = (arg & 0xff); offset *= 134; }' in text
               for text in candidates)
