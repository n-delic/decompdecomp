from decompnet.app.recent_capture_repairs import variants


def family(source,kind,asm='',header='',callees=''):
    return [text for label,text in variants(source,'f','mips',asm,header,callees) if label==kind]


def test_shared_store_literals_do_not_move_stores():
    source='void f(int *a, int *b) { *a = -1; *b = -1; }'
    choices=family(source,'shared_store_constant')
    assert len(choices)==2
    assert '*a = __repair_constant; *b = __repair_constant;' in choices[0]
    assert not family(source.replace('int *a','volatile int *a'),'shared_store_constant')


def test_nested_halfword_cast_hypothesis_requires_known_alias():
    source='int f(int sx,int add) { return ((u16)(sx - 0x39)) + add; }'
    assert not family(source,'nested_halfword_arithmetic')
    choices=family(source,'nested_halfword_arithmetic',header='typedef unsigned short u16;')
    assert '(u16)sx + (u16)-0x39 + add' in choices[0]


def test_overlay_alias_requires_exact_unambiguous_target_call():
    source='void f(void) { func_80100000_ovl_12(); }'
    assert not family(source,'overlay_callee_alias')
    choices=family(source,'overlay_callee_alias','jal func_80100000_overlay_027\nnop')
    assert 'func_80100000_overlay_027();' in choices[0]
    assert not family(source,'overlay_callee_alias','jal func_80100000_overlay_027\njal func_80100000_overlay_028')


def test_array_extent_requires_complete_callee_and_stack_argument():
    source='void f(void) { int a[4]; fill(a); use(a[0]); }'
    callee='void fill(int *p) { p[0]=1; p[1]=2; }'
    asm='addiu $sp, $sp, -0x38\naddiu $a0, $sp, 0x10'
    choices=family(source,'callee_array_extent',asm,callees=callee)
    assert 'int a[2];' in choices[0]
    assert not family(source,'callee_array_extent',callees=callee)
    assert not family(source.replace('a[0]','a[3]'),'callee_array_extent',asm,callees=callee)
    assert not family(source,'callee_array_extent',asm,callees=callee.replace('p[1]=2','escape(p)'))


def test_argument_home_uses_abi_frame_evidence_without_fixed_addresses():
    source='void f(int a, int b) { consume(b); }'
    choices=family(source,'argument_home_slot','addiu $sp, $sp, -0x18\nsw $a1, 0x1c($sp)')
    assert 'int *__repair_home1 = &b;' in choices[0]
    assert 'consume((*__repair_home1))' in choices[0]
    assert not family(source,'argument_home_slot','addiu $sp, $sp, -0x18\nsw $a1, 0x14($sp)')


def test_forwarding_requires_known_arity_and_untouched_registers():
    source='void f(int a,int b) { callee(); }'
    callee='void callee(int x,int y) { sink(x,y); }'
    assert 'callee(a, b)' in family(source,'untouched_argument_forwarding','jal callee\nnop',callees=callee)[0]
    assert not family(source,'untouched_argument_forwarding','jal callee\nmove $a0, $zero',callees=callee)
