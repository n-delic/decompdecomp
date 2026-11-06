from decompnet.app import agent_evidence, lifetime_repairs
from tests.test_claims import _game


def test_array_index_width_does_not_touch_dynamic_or_side_effecting_indices():
    source='extern unsigned char table[];int f(int index){return table[index];}'
    out=lifetime_repairs.variants(source,'f')
    assert any('table[(unsigned short)index]' in s for f,s in out if f=='array_index_width')
    assert not lifetime_repairs.variants(source.replace('table[index]','table[next()]'),'f')


def test_split_scalar_lifetime_keeps_first_segment_and_next_definition():
    source='void f(){double rate;rate=one;use(rate);rate=two;rate*=v;use(rate);rate=three;use(rate);}'
    out=[s for f,s in lifetime_repairs.variants(source,'f') if f=='split_scalar_lifetime']
    assert out and 'rate=one;use(rate)' in out[0]
    assert '__repair_rate_1=two' in out[0] and '__repair_rate_1*=v' in out[0]
    assert 'rate=three;use(rate)' in out[0]
    assert not lifetime_repairs.variants(source.replace('use(rate);','use(&rate);'),'f')


def test_pointer_capture_precedes_store_and_preserves_lvalue_reload():
    source='void f(void*p){if(flag){*(int*)p=0;global=0;M2C_FIELD(*(void **)((char*)p+72),int*,32)=call(M2C_FIELD(*(void **)((char*)p+72),short*,2));}}'
    out=[s for f,s in lifetime_repairs.variants(source,'f') if f=='pointer_before_store']
    assert out and out[0].index('__repair_early_ptr =')<out[0].index('*(int*)p=0')
    assert 'M2C_FIELD(*(void **)((char*)p+72),int*,32)' in out[0]
    assert 'M2C_FIELD(__repair_early_ptr,short*,2)' in out[0]
    assert not any(f=='pointer_before_store' for f,_ in lifetime_repairs.variants(source.replace('global=0;','prepare();global=0;'),'f'))


def test_repeated_output_budget_catches_alternating_old_outputs(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');net.claim_row(a,'game','f');fn=net._function('f','game')
    net.set_selection('game','a',{'unchanged_compiles':2})
    net._agent_outcomes={}
    def result(value):
        net._agent_outcomes[a.id,fn['id']]={'score':1,'rows':[('r','target',value)]}
        return agent_evidence.track(net,a,'game',fn)
    assert '0/2' in result('A')
    assert '0/2' in result('B')
    assert '1/2' in result('A')
    assert 'limit reached' in result('B')


def test_new_families_are_reachable_by_shared_local_repair_pipeline():
    from decompnet.app import draft_repairs, local_experiments
    source='extern unsigned char table[];int f(int index){return table[index];}'
    labels={}
    choices=draft_repairs.variants(source,'f','mips',labels=labels)
    assert any(labels[s]=='array_index_width' for s in choices)
    assert {'array_index_width','split_scalar_lifetime','pointer_before_store'}<=set(local_experiments.FAMILIES)
