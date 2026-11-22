"""store_temp_inline, entry_constant_local and join_clamp_sum_repairs (decomprepair 0.5.630-0.5.631) were released
without a caller; tests/test_library_family_wiring.py found them. Each reaches the draft repair path (and so the Deep
Permuter's beam) and is known to the agents."""
import pytest

from decompnet.app import draft_repairs

STORE_TEMP = """\
void f(u8 *base, int idx, int n) {
    u32 temp;
    temp = (idx * 8) + *(u8 **)(base + 4);
    *(u16 *)(base + n * 4) = temp + (temp >> 1) + (temp << 2);
}
"""

ENTRY_CONSTANT = """\
void g(u8 *p, int n) {
    int a;
    int b;
    a = 1;
    b = 0;
    while (n > 0) {
        if (p[0] != 0xFF) { b++; }
        if (p[1] == 0xFF) { a++; }
        n--;
    }
}
"""

CLAMP = """\
void h(u8 *out) {
    u8 v;
    v = out[0];
    if ((v + 1) < (0xFFU + 1)) { out[1] = v; }
}
"""


@pytest.mark.parametrize('family, source, name', [
    ('store_temp_inline', STORE_TEMP, 'f'),
    ('entry_constant_local', ENTRY_CONSTANT, 'g'),
    ('plus_one_clamp_compare', CLAMP, 'h'),
])
def test_the_family_is_proposed_labelled_and_known(family, source, name):
    labels = {}
    draft_repairs.variants(source, name, 'mips', labels=labels, limit=None)
    assert family in set(labels.values())
    assert family in draft_repairs.AGENT_FAMILIES


def test_crossing_scope_unwrap_is_offered_and_known():
    from decomprepair import crossing_scope_unwrap
    source = """\
void k(int *p, int n) {
    int i;
    i = 0;
    do {
    scan:
        p[i] = n;
        i++;
        if (i < n) goto scan;
    } while (0);
}
"""
    children = crossing_scope_unwrap.variants(source, 'k')
    result = draft_repairs.variants(source, 'k', 'mips', labels={}, limit=None)
    assert children and all(text in result for _, text in children)
    assert 'crossing_scope_unwrap' in draft_repairs.AGENT_FAMILIES
