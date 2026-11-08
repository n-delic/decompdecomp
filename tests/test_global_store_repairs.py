from decompnet.app import global_store_repairs as repair


def test_scalar_global_address_stays_live_across_call_with_same_width_result():
    source='extern signed char state; int call(int,int); int f(int x){signed char value; value=state; state=call(x,value);return 0;}'
    rows=repair.variants(source,'f')
    assert len(rows)==1 and rows[0][0]=='scalar_global_address'
    assert 'signed char *__repair_scalar_address = &state;' in rows[0][1]
    assert 'value = call(x,*__repair_scalar_address); *__repair_scalar_address = value;' in rows[0][1]
    assert not repair.variants(source.replace('signed char state','volatile signed char state'),'f')
    assert not repair.variants(source.replace('return 0','return value'),'f')
    assert not repair.variants(source.replace('call(x,value)','call(other(),value)'),'f')


def test_split_mask_stores_preserve_order_and_never_duplicate_side_effecting_addresses():
    source='void f(char *p){*(int *)(p+244)=(*(int *)(p+244)&0xFFEFFFFF)&0xFFFF7FFF;}'
    rows=repair.variants(source,'f')
    assert len(rows)==2 and all(f=='split_mask_stores' for f,_ in rows)
    assert any('*(unsigned int *)(p+244) &= 0xFFEFFFFF; *(unsigned int *)(p+244) &= 0xFFFF7FFF;' in text for _,text in rows)
    assert not repair.variants(source.replace('p+244','p+++244'),'f')
    assert not repair.variants(source.replace('p+244','get()+244'),'f')
    assert not repair.variants(source.replace('int *','volatile int *'),'f')
