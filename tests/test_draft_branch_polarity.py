"""The `else if` arm swap (decomprepair.branch_polarity_repairs) is offered by the draft repair pass."""
from decompnet.app import draft_repairs


def test_draft_pass_swaps_an_else_if_chain():
    source = """void f(int a, int b) {
    if (a & 4) {
        first();
    } else if (b) {
        second();
    }
}
"""
    labels = {}
    texts = draft_repairs.variants(source, 'f', 'mips', '', (), labels, limit=None)
    swapped = [text for text in texts if labels.get(text) == 'branch_polarity']
    assert any(text.index('if (b)') < text.index('first();') for text in swapped)
    assert 'branch_polarity' in draft_repairs.AGENT_FAMILIES
