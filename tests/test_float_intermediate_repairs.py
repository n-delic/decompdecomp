import pytest

from decompnet.app import draft_repairs
from decompnet.app import float_intermediate_repairs as repair

SOURCE = 'void f(int n, float a, float b, int *out) { float value; float product; value=(float)n; *out=(int)((a * value) * b); }'


def test_product_store_preserves_cast_product_order_and_other_functions():
    source = SOURCE.replace('*out=', 'out->result=')
    variants = repair.variants(source, 'f')
    assert len(variants)==2
    assert all('(float)n' in text and '* b)' in text for _,text in variants)
    assert any('product = a * (float)n;' in text and '(product * b)' in text for _,text in variants)
    labels = {}
    found = draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert any(labels[text]=='float_product_intermediate' for text in found)
    assert not repair.variants('void f() { puts("'+source.replace('"','')+'"); }','f')


def test_an_earlier_branch_lifetime_can_reuse_a_now_dead_product_local():
    source = 'void f(int n,float a,float b) {float value;float product; if(n) {product=1;use(product);value=(float)n;use(value);} else {value=(float)n;out=(int)((a * value) * b);} }'
    assert any('product = a * (float)n;' in text for _,text in repair.variants(source,'f'))
    assert not repair.variants(source.replace(';} }','; use(value);} }'),'f')


@pytest.mark.parametrize('source', [
    SOURCE,  # unsupported dereference LHS stays opaque
    SOURCE.replace('*out=', 'out=').replace('a * value', 'call() * value'),
    SOURCE.replace('*out=', 'out=').replace('value=(float)n', 'value=(float)n++'),
    SOURCE.replace('*out=', 'out=').replace('float value;', 'volatile float value;'),
    SOURCE.replace('*out=', 'out=').replace(' * b);', ' * value);'),
    SOURCE.replace('*out=', 'out=').replace('value=(float)n;', 'value=(float)n; inspect(&value);'),
])
def test_side_effects_escaped_temporaries_and_extra_uses_are_not_guessed(source):
    assert not repair.variants(source,'f')
