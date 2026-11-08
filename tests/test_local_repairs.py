"""Local scheduling repairs and routing safeguards use synthetic source only."""







from decompnet.app import pattern_repairs, temporary_repairs


def test_else_line_join_requires_debug_flags_and_masks_comments():
    source='void f(){if(p){send();\n } else {other();}}'
    assert any('send(); } else' in v for _,v in pattern_repairs.variants(source,'f',True))
    assert not any('send(); } else' in v for _,v in pattern_repairs.variants(source,'f',False))
    assert not pattern_repairs.variants('void f(){/* send();\n } else */}', 'f',True)


def test_float_computation_hoisted_before_store_with_typed_declaration():
    source='void f(){f32 x; if(p){FIELD(p,f32*,4)=x; send((f32)((f64)x * scale));}} void other(){}'
    out=temporary_repairs.variants(source,'f')
    assert len(out)==2
    assert all('f64 __repair_value;' in s and '__repair_value = (f64)x * scale;' in s and '((__repair_value))' not in s for s in out)
    assert all(s.index('__repair_value =') < s.index('FIELD') and s.endswith('void other(){}') for s in out)
    for source in ('void f(){x=next(); send((f64)y * scale);}',
                   'void f(){y=x; send((f64)y * scale);}',
                   'void f(){store=x; if(p)send((f64)y * scale);}',
                   'void f(){volatile int p; store=x;send((f64)y * scale);}'):
        assert not temporary_repairs.variants(source,'f')


def test_scalar_parameter_split_reuses_only_same_type_dead_local():
    source='void f(s32 a,s32 b){s32 t; u32 u; a=b*a; use(a);}'
    out=temporary_repairs.variants(source,'f')
    assert any('t = b*a; a=t;' in s for s in out)
    assert all('u = b*a' not in s for s in out)
    assert not temporary_repairs.variants('void f(s32 a){s32 t;a=a*2;use(t);}','f')
    assert not temporary_repairs.variants('void f(s32 a){s32 t;a=next()*2;}','f')
