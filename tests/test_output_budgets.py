

from decompnet.app import load_pair_repairs


def test_pair_candidates_keep_both_loads_and_reject_calls():
    source='void f(){a=*(u16 *)(manager+26);b=(*(s32 *)((char *)(node)+4));flag=b!=0;if(a){send();}}'
    out=load_pair_repairs.variants(source,'f')
    assert len(out)==1 and 'volatile u16' in out[0] and 'volatile s32' in out[0]
    assert not load_pair_repairs.variants(source.replace('manager+26','get()+26'),'f')
    assert not load_pair_repairs.variants(source.replace('b=','send();b='),'f')
