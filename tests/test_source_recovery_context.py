


from decompnet.app import helper_expansion


def test_small_initializer_expansion_is_bounded_and_rejects_repeated_side_effects():
    header='static void setup(Item *p){p->head=0; p->node.kind=2; p->owner=&owner;}'
    source='void f(Item *x){setup(x);}'
    variants=helper_expansion.variants(source,'f',header)
    assert len(variants)==1 and '(x)->node.kind=2' in variants[0][1]
    assert not helper_expansion.variants(source.replace('setup(x)','setup(x++)'),'f',header)
    assert not helper_expansion.variants(source,'f',header.replace('p->head=0','p->head=read()'))
    assert not helper_expansion.variants(source,'f',header.replace('p->head=0','p=other'))
    assert not helper_expansion.variants(source.replace('Item *x','volatile Item *x'),'f',header)
    assert helper_expansion.variants(source,'f','#define KIND 2\n'+header.replace('kind=2','kind=KIND'))
    assert not helper_expansion.variants(source,'f','#define KIND read()\n'+header.replace('kind=2','kind=KIND'))
