"""Homemade lifetime/store examples; no game code or target data."""
import pytest

from decompnet.app import draft_repairs
from decomprepair import pointer_result_repairs, record_lifetime_recovery, record_store_bundles

HEADER='typedef signed short s16; typedef unsigned short u16; typedef signed int s32;'
POINTER='''typedef struct { int id; } Item;
Item *take(void); void use(Item *p);
void f(int n) {
 Item *p;
 if(n) { Item *q; q=take(); use(q); }
 p=take(); use(p);
}'''
STORES='''typedef struct { s16 x; s16 y; u16 w; u16 h; } Box;
void f(Box *p, int w, int h) {
 Box r;
 r.h=h; r.w=w;
 p->w=w; p->h=h;
}'''


def test_nested_call_result_reuses_enclosing_initialized_disjoint_storage():
    out=pointer_result_repairs.variants(POINTER,'f')
    assert len(out)==1
    assert 'Item *q;' not in out[0][1] and 'q=take()' not in out[0][1]
    assert 'p=take(); use(p);' in out[0][1]


@pytest.mark.parametrize('source',[
 POINTER.replace('p=take(); use(p);','use(p); p=take(); use(p);'),
 POINTER.replace('use(q);','use(q); use(p);'),
 POINTER.replace('use(q);','escape(&q); use(q);'),
 POINTER.replace('Item *q;', 'volatile Item *q;'),
 POINTER.replace('q=take();','q=(Item *)1;'),
 POINTER.replace('q=take();','if(n==2) q=take();'),
 POINTER.replace('Item *p;', 'Item *p=take();'),
 POINTER.replace('Item *q;', 'Other *q;'),
 '#define q hidden\n'+POINTER,
 '#define hidden use(q)\n'+POINTER.replace('use(q);','hidden;'),
])
def test_reuse_rejects_live_uninitialized_escaped_opaque_or_incompatible(source):
    assert not pointer_result_repairs.variants(source,'f')


def test_sibling_blocks_cannot_supply_storage():
    source=POINTER.replace(' Item *p;', '').replace(' p=take(); use(p);',' { Item *p; p=take(); use(p); }')
    assert not pointer_result_repairs.variants(source,'f')


def test_bundle_canonicalizes_value_and_pointer_records_together():
    out=record_store_bundles.variants(STORES,'f',HEADER,'mips')
    assert len(out)==2
    assert any('r.w=w; r.h=h;' in s and 'p->w=w; p->h=h;' in s for _,s in out)
    assert any('r.h=h; r.w=w;' in s and 'p->h=h; p->w=w;' in s for _,s in out)


@pytest.mark.parametrize('source',[
 STORES.replace('r.w=w;', 'emit(); r.w=w;'),
 STORES.replace('struct', 'union'),
 STORES.replace('u16 w;', 'volatile u16 w;'),
 STORES.replace('r.w=w;', 'r.w=read();'),
 '#define w hidden\n'+STORES,
 STORES.replace('Box r;', 'Other r;'),
 STORES.replace('Box r;', 'Box r; escape(&w);'),
])
def test_bundle_needs_multiple_ordinary_known_independent_sites(source):
    assert not record_store_bundles.variants(source,'f',HEADER,'mips')


def test_shared_generator_exposes_new_families_without_architecture_specific_names():
    for arch in ('mips','ppc','arm','x86'):
        labels={}
        draft_repairs.variants(POINTER,'f',arch,labels=labels,limit=None)
        assert 'pointer_result_reuse' in labels.values()
        labels={}
        draft_repairs.variants(STORES,'f',arch,header=HEADER,labels=labels,limit=None)
        expected={s for _,s in record_store_bundles.variants(STORES,'f',HEADER,arch)}
        assert expected <= set(labels)  # Identical one-site repairs may share an older family label.


def test_combined_recipe_is_bounded_and_keeps_all_three_required_edges():
    from tests.test_narrow_store_repairs import DEAD
    source=DEAD.replace('void f(Box *p, Entry *entry)', 'Entry *take(void); void use(Entry *);\nvoid f(Box *p, Entry *entry)')
    source=source.replace(' s32 x =', ' Entry *result;\n if(p) { Entry *inner; inner=take(); use(inner); }\n result=take(); use(result);\n s32 x =')
    source=source.replace(' entry->id = 2;', ' Box r; r.h=4; r.w=2;\n entry->id = 2;')
    out=record_lifetime_recovery.variants(source,'f',HEADER,'mips')
    assert 0<len(out)<=4
    assert all('Entry *inner;' not in s and '__repair_next_field' in s and '(s16)(x + half)' in s for _,s in out)
    from decompnet.app import iterative_repairs
    from decomprepair import narrow_store_repairs
    selected=iterative_repairs.targeted_variants(source,{},dict(name='f',asm=''),'mips',[],HEADER,tagged=True,limit=4)
    assert selected[:len(out)]==out
    drafts=list(narrow_store_repairs.machine_drafts([('raw',source)],'f',HEADER,'mips'))
    assert drafts[0]==('raw',source) and len(drafts)<=4
    assert any(f=='record_lifetime_sequence' for f,_ in drafts[1:])
