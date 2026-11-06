"""Synthetic source/assembly only; no game data or library source."""
from decompnet.app import draft_repairs
from decompnet.app import target_local_repairs as repairs


def test_target_delay_slot_argument_requires_local_prototype_and_capture():
    source='void draw(void); void *select(int); void f(void) { void *p; p=select(2); draw(); }'
    asm='/* 0 80001000 00000000 */ jal select\n/* 4 80001004 00000000 */ nop\n/* 8 80001008 00000000 */ jal draw\n/* C 8000100C 00000000 */ lw $a0, 0x178($v0)'
    result=repairs.variants(source,'f','mips',asm)
    candidates=[s for f,s in result if f=='target_call_argument']
    assert len(candidates)==1
    assert 'void draw(int);' in candidates[0] and 'draw(*((int *)((char *)p + 376)))' in candidates[0]
    assert not repairs.call_arguments(source,'f',asm,'void draw(void);')
    assert not repairs.call_arguments(source.replace('p=select(2);','select(2);'),'f',asm)
    assert not repairs.call_arguments(source,'f',asm.replace('$a0','$a1'))
    assert not repairs.variants(source,'f','ppc',asm)


def test_alias_requires_unscaled_known_byte_address_and_unsigned_global():
    source='extern u8 D_80001000[]; extern u32 D_80001020; void f(void) { void *p; p=D_80001000+0x20; use(D_80001020); }'
    result=repairs.aliases(source,'f')
    assert len(result)==1 and 'use((*((unsigned int *)p)))' in result[0][1]
    for changed in (source.replace('u8 D_','u32 D_',1),source.replace('use(D_80001020);','D_80001020=2;'),source.replace('use(', 'p=0; use(')):
        assert not repairs.aliases(changed,'f')


def test_single_field_layout_shares_layout_between_distinct_call_results():
    source='void f(void) { void *a; void *b; a=get(1); use(*((s32 *) (((char *) a) + 0x178))); b=get(2); use(*((void **) (((char *) b) + 0x178))); }'
    result=repairs.records(source,'f','mips')
    assert len(result)==1
    assert 'unsigned char pad[376]; int value;' in result[0][1]
    assert result[0][1].count(')->value')==2
    assert not repairs.records(source,'f','x86_64')


def test_raw_record_inverse_checks_actual_layout():
    source='typedef struct { unsigned char pad_0[14]; unsigned short field_E; } R; void f(void) { use(((R*)p)->field_E); }'
    result=repairs.records(source,'f','mips')
    assert '*((unsigned short *)((char *)p + 14))' in result[0][1]
    assert not repairs.records(source.replace('[14]','[12]'),'f','mips')


def test_boolean_mask_elimination_preserves_later_other_assignment():
    source='void f(void) { int x; u8 y; x=y & 0xFF; if(x != 0) use(); x=get(); other(x); }'
    result=repairs.masks(source,'f')
    assert len(result)==1
    assert 'if(y != 0)' in result[0][1] and 'x=get(); other(x)' in result[0][1]
    assert not repairs.masks(source.replace('if(x != 0)','if(x + 1)'),'f')
    assert not repairs.masks(source.replace('u8 y','u32 y'),'f')


def test_split_result_gives_reused_local_a_fresh_lifetime():
    source='void f(void) { int x; x=first(); use(x); x=second(); use(x); }'
    result=repairs.split_results(source,'f')
    assert result and any('x=first(); use(x); __repair_result_x=second(); use(__repair_result_x)' in s for _,s in result)
    assert not repairs.split_results(source.replace('int x;','int x; escape(&x);'),'f')


def test_guards_caps_and_pipeline_registration():
    source='void f(void) { int x; u8 y; x=y & 255; if(x != 0) use(); }'
    assert not repairs.variants(source.replace('int x','volatile int x'),'f','mips')
    labels={}
    choices=draft_repairs.variants(source,'f','mips',labels=labels,limit=None)
    assert any(labels[s]=='direct_mask_condition' for s in choices)
    assert set(repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)
    assert len(repairs.variants(source,'f','mips'))<=repairs.LIMIT


def test_call_diagnostics_are_advisory_and_reassignment_is_rejected():
    from decompnet.app.source_diagnostics import hints
    source='void draw(void); void *select(int); void f(void) { void *p; p=select(2); if(flag) draw(); }'
    asm='/* 0 */ jal select\n/* 4 */ nop\n/* 8 */ jal draw\n/* C */ lw $a0, 0x178($v0)'
    assert repairs.call_arguments(source,'f',asm)
    assert any('first call argument' in s for s in hints(source,'f',asm,'mips'))
    assert not repairs.call_arguments(source.replace('if(flag)', 'p=other; if(flag)'),'f',asm)
    assert not repairs.call_arguments(source.replace('if(flag)', 'other(); if(flag)'),'f',asm)


def test_identity_expansion_is_scoped_to_requested_definition():
    source='extern u8 D_80001000[]; extern u32 D_80001020; static inline u32 id(u32 x) { return (u32)x; } void f(void) { void *p; p=D_80001000+32; use(id(*((u32 *) (((char *) p) + 0))),D_80001020); } void other(void) { use(id(3)); }'
    candidates=repairs.aliases(source,'f')
    assert candidates and 'use(id(3))' in candidates[0][1]
    assert 'use(id(*' not in candidates[0][1]


def test_call_result_reuse_requires_dead_compatible_local_and_same_callee():
    source='void f(void) { int a; int b; a=alloc(); use(a); b=alloc(); use(b); }'
    candidates=repairs.value_lifetimes(source,'f')
    assert any(f=='call_result_reuse' and 'a=alloc(); use(a); a=alloc(); use(a)' in s for f,s in candidates)
    assert not repairs.value_lifetimes(source.replace('use(b);','use(b); use(a);'),'f')
    assert not repairs.value_lifetimes(source.replace('b=alloc()','b=other()'),'f')


def test_field_value_lifetime_keeps_later_pointer_uses():
    source='typedef struct { unsigned char pad[20]; int value; } R; void f(void) { void *p; void *view; p=((R *)obj)->value; view=(void *)p; use(view); p=next(); use(p); }'
    candidates=repairs.value_lifetimes(source,'f')
    assert any(f=='field_value_lifetime' and '__repair_value_p=((R *)obj)->value;' in s and 'p=next(); use(p)' in s for f,s in candidates)


def test_missing_assembly_is_not_a_call_hint():
    from decompnet.app.source_diagnostics import hints
    assert repairs.call_arguments('void f(void) {}','f',None)==[]
    assert hints('void f(void) {}','f',None,'mips')==[]


def test_target_field_load_order_requires_matching_offset_pair():
    from decompnet.app import field_load_repairs
    source='void f(void) { u32 a; a=*((u32 *) (((char *) p) + 8)); if(a < *((u32 *) (((char *) p) + 4))) use(); }'
    asm='/* 0 */ lw $v0, 4($a3)\n/* 4 */ lw $v1, 8($a3)'
    candidates=field_load_repairs.variants(source,'f','mips',asm)
    assert candidates and any(s.index('__repair_load_4 =')<s.index('a=') for _,s in candidates)
    assert not field_load_repairs.variants(source,'f','mips',asm.replace('4($a3)','12($a3)'))
    assert not field_load_repairs.variants(source,'f','ppc',asm)
    assert not field_load_repairs.variants(source.replace('u32 a','volatile u32 a'),'f','mips',asm)
