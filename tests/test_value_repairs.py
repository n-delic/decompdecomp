"""Repair suggestions become bounded hypotheses and go through the existing agent judge."""






from decompnet.app import value_repairs


def test_integer_formal_width_requires_mask_or_narrow_store_evidence():
    source='void other(s32 a){} void f(s32 a,s32 b,void *p){send(a&0xFFFF);*(s16 *)(p+4)=a;}'
    out=value_repairs.variants(source,'f')
    assert any('void f(u16 a,s32 b' in s for _,s in out)
    assert any('void f(s16 a,s32 b' in s for _,s in out)
    assert all(s.startswith('void other(s32 a){}') for _,s in out)
    assert not value_repairs.variants('void f(s32 a){send(a);}','f')
    assert not value_repairs.variants('void f(s32 *a){send(*a&0xFFFF);}','f')


def test_pointer_to_scalar_lifetime_split_keeps_volatile_load_in_place():
    source='void f(){volatile s32 *p;s32 *base;s32 field;base=read();p=base;do{base=(s32 *)p[10];i++;if((s32)base==n)use(p);}while(i<n);}'
    out=[s for k,s in value_repairs.variants(source,'f') if k=='value_lifetime']
    assert len(out)==1 and 'field = p[10];i++;if(field==n)' in out[0]
    assert out[0].count('p[10]')==1 and 'volatile s32 *p' in out[0]
    assert not any(k=='value_lifetime' for k,_ in value_repairs.variants(source.replace('use(p)','use(base)'),'f'))
    assert not any(k=='value_lifetime' for k,_ in value_repairs.variants(source.replace('base=read();','field=read();base=read();'),'f'))


def test_sum_shapes_reject_destination_dependence_and_calls():
    source='void f(){void *q;q=((n%(*p))*8)+(char *)(p+8);}'
    assert any('q = (char *)(((n%(*p))*8)); q += (s32)((char *)(p+8));' in s for k,s in value_repairs.variants(source,'f') if k=='sum_shape')
    source='void f(){send(off+*(s32 *)(base+4));}'
    assert any('send(*(s32 *)(base+4) + off)' in s for _,s in value_repairs.variants(source,'f'))
    for source in ('void f(){s32 x;x=a+x;}', 'void f(){s32 x;x=a+next();}', 'void f(){volatile s32 x;x=a+b;}'):
        assert not any(k=='sum_shape' for k,_ in value_repairs.variants(source,'f'))
