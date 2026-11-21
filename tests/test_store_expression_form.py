"""decomp-repair's store_expression_form is offered by the app's repair chain."""
from decompnet.app import draft_repairs

SOURCE = '''void f(const s16 *a, f32 *m)
{
    f32 c, s, t, u;
    f32 cs, ss;

    c = cosf(a[0]);
    s = sinf(a[0]);
    cs = c * s;
    ss = s * s;
    t = cs * c;
    u = ss * c;
    m[1] = u;
    m[0] = t;
}
'''


def test_the_chain_offers_the_stores_written_out_in_index_order():
    # A rotation-matrix builder (GCC 2.7.2): 15 with the temporaries, exact with them written out and the stores in
    # index order; the real proof is the user's deep pass on such a function.
    out = draft_repairs.variants(SOURCE, 'f', 'mips', limit=400)
    assert 'store_expression_form' in draft_repairs.AGENT_FAMILIES
    assert any('m[0] = (c * s) * c;\n    m[1] = (s * s) * c;' in text for text in out)
