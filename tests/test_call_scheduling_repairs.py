from decompnet.app import (
    allocation_evidence,
    call_scope_repairs,
    debug_initializer_repairs,
    draft_repairs,
    phase_union_repairs,
    repair_ranking,
)

HEADER = 'typedef unsigned char u8; typedef unsigned int u32; typedef int s32; typedef float f32; typedef struct Sound Sound;'
FLAGS = ['-mgp32','-O2','-g3']


def test_scope_keeps_a_call_once_and_preserves_outer_loop_control():
    source = 'void f(int n) { float pitch; pitch=1.0f; while(n--) { if(n) update(n,pitch); else continue; } }'
    candidates = call_scope_repairs.variants(source,'f')
    assert len(candidates)==1
    text=candidates[0][1]
    assert 'if(n) do { update(n,pitch); } while (0); else continue;' in text
    assert text.count('update(n,pitch)')==1
    assert not call_scope_repairs.variants(text,'f')


def test_scope_does_not_capture_macro_control_or_rewrite_assignment_calls():
    source = 'void f(void) { int n; n=read(); WRAP(n); update(JUMP()); }'
    header = '#define WRAP(n) if(n) break\n#define JUMP() ({ continue; 0; })'
    assert not call_scope_repairs.variants(source,'f',header)
    assert not call_scope_repairs.variants('void f(void) { update(__LINE__); }','f')


def test_debug_group_preserves_effect_order_and_requires_debug_metadata():
    source = 'void f(void) { int a; int b; a=1;\n b=2;\n consume(a,b); }'
    assert not debug_initializer_repairs.variants(source,'f',[])
    assert not debug_initializer_repairs.variants(source,'f',['-g0'])
    rows = debug_initializer_repairs.variants(source,'f',FLAGS)
    assert any('a=1; b=2;' in text for _,text in rows)
    for body in ['a=read();\n b=2;', 'a=1; // comment\n b=2;', 'a=1;\n WRITE(b);']:
        original = source.replace('a=1;\n b=2;',body)
        assert not debug_initializer_repairs.variants(original,'f',FLAGS)


def test_constant_stage_groups_private_address_math_and_retains_literal_type():
    source = 'void f(void) { double big; float *p; float *q; big=limit;\n p=&volume;\n q=p+1;\n consume(100.0f); consume(100.0f); }'
    rows = debug_initializer_repairs.variants(source,'f',FLAGS)
    text = next(text for family,text in rows if family=='debug_constant_initialization' and 'p=&volume; q=p+1; big=limit;' in text)
    assert 'float __repair_float_constant;' in text
    assert '__repair_float_constant = 100.0f;' in text
    assert text.count('consume(__repair_float_constant)')==2
    assert not debug_initializer_repairs.variants(source,'f',FLAGS,'#define HERE __LINE__')
    # The address derived from p cannot precede its definition.
    assert not any('q=p+1; p=&volume;' in text for _,text in rows)


def test_pointer_word_union_only_when_both_phases_are_initialized_and_disjoint():
    source = 'void f(void) { Sound *sound; u8 gear; sound=base; use(sound); gear=input; if(gear>2) gear=2; volume(gear); }'
    rows = phase_union_repairs.variants(source,'f','mips',FLAGS,HEADER)
    assert len(rows)==1
    text=rows[0][1]
    assert 'union { Sound * pointer; unsigned int value; } __repair_phase_storage;' in text
    assert 'use(__repair_phase_storage.pointer)' in text
    assert '__repair_phase_storage.value = (u8)(input);' in text
    assert 'volume(((u8)__repair_phase_storage.value))' in text
    assert not phase_union_repairs.variants(source,'f','x86',[],HEADER)
    assert not phase_union_repairs.variants(source.replace('volume(gear);','volume(gear); use(sound);'),'f','mips',FLAGS,HEADER)
    assert not phase_union_repairs.variants(source.replace('sound=base;',''),'f','mips',FLAGS,HEADER)
    assert not phase_union_repairs.variants(source.replace('volume(gear);','volume(gear); escape(&gear);'),'f','mips',FLAGS,HEADER)


def test_phase_union_rejects_hidden_uses_shadowing_and_unknown_alias_width():
    source = 'void f(void) { Sound *sound; u8 gear; sound=base; use(sound); gear=input; volume(gear); }'
    assert not phase_union_repairs.variants(source,'f','mips',FLAGS,HEADER+'\n#define hidden sound')
    assert not phase_union_repairs.variants(source,'f','mips',FLAGS,HEADER.replace('unsigned char u8','unsigned short u8'))
    assert not phase_union_repairs.variants(source.replace('volume(gear);','{ u8 gear; gear=1; volume(gear); }'),'f','mips',FLAGS,HEADER)


def test_register_swap_recommends_source_repairs_without_altering_rows():
    rows = [('r','move $s0, $a0','move $s1, $a0'),('r','move $a1, $s1','move $a1, $s0')]
    assert allocation_evidence.diagnose(rows,'mips')['register_cycles']==[['$s0','$s1']]
    assert 'pointer_integer_phase_union' in allocation_evidence.plan(rows,'mips')['families']
    families=['constant_local','pointer_integer_phase_union','split_integer_lifetime']
    assert repair_ranking.order(families,rows)[0]=='pointer_integer_phase_union'
    assert rows[0][1]=='move $s0, $a0'


def test_new_families_reach_shared_pipeline():
    labels={}
    source = 'void f(void) { float pitch; pitch=2.0f; update(pitch); }'
    rows=draft_repairs.variants(source,'f','mips',flags=FLAGS,header=HEADER,labels=labels,limit=None)
    assert any(labels.get(text)=='call_one_shot_scope' for text in rows)
    assert set(call_scope_repairs.FAMILIES+debug_initializer_repairs.FAMILIES+phase_union_repairs.FAMILIES)<=set(draft_repairs.AGENT_FAMILIES)


def test_overridden_debug_and_pointer_width_flags_disable_sensitive_repairs():
    source='void f(void) { int a; int b; a=1;\n b=2; use(a,b); }'
    assert not debug_initializer_repairs.variants(source,'f',['-g3','-g0'])
    assert debug_initializer_repairs.variants(source,'f',['-g0','-g3'])
    source='void f(void) { Sound *sound; u8 gear; sound=base; use(sound); gear=input; volume(gear); }'
    assert not phase_union_repairs.variants(source,'f','mips',FLAGS+['-mgp64'],HEADER)
    assert not phase_union_repairs.variants(source,'f','mips',FLAGS,HEADER+'\n#define u8 unsigned long long')


def test_initializer_rotation_never_reorders_volatile_private_assignments():
    source='void f(void) { f32 a; f32 b; a=first;\n b=2; use(a,b); }'
    rows=debug_initializer_repairs.variants(source,'f',FLAGS,'typedef volatile float f32;')
    assert not any('b=2; a=first;' in text for _,text in rows)
