"""The sentinel-constant-local family (decomp-repair sentinel_local_repairs) reaches the drafts and the Deep Permuter's beam."""
from decompnet.app import draft_repairs

SOURCE = """\
s32 g(s32);
s32 f(s32 a, s32 b)
{
    s32 v;
    v = g(a);
    if (v == -1) {
        b = g(b);
    }
    if (b != (-1)) {
        return b;
    }
    return v;
}
"""


def test_a_literal_compared_in_several_places_is_proposed_as_a_local():
    labels = {}
    texts = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    proposed = {text: family for text, family in labels.items() if family in ('sentinel_constant_local', 'sentinel_constant_split')}
    assert proposed and all(text in texts for text in proposed)
    assert any('v == sentinel_neg0' in text and 'b != sentinel_neg0' in text for text in proposed)
    assert {'sentinel_constant_local', 'sentinel_constant_split'} <= set(draft_repairs.AGENT_FAMILIES)
