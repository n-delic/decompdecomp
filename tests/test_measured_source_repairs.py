from decompnet.app import measured_source_repairs as repairs
from decompnet.app import reference_context_recovery as contexts
from decompnet.app.repair_ranking import private_abi_order
from decompnet.arch import mips_source_recovery as mips


def test_line_join_requires_measured_instruction_order_only():
    source='void f(int *p,int n){\n p=p+1;\n *p=n;\n}'
    rows=[('i','lw $t0, 4($sp)','lw $t1, 0($a0)'),('i','lw $t1, 0($a0)','lw $t0, 4($sp)')]
    result=repairs.statement_lines(source,'f','',rows)
    assert any('p=p+1; *p=n;' in s for _,s,_ in result)
    assert not repairs.statement_lines(source,'f','',rows[:1])
    assert not repairs.statement_lines(source.replace('\n *p',' // keep\n *p'),'f','',rows)
    assert not repairs.statement_lines(source.replace('*p=n','*p=__LINE__'),'f','',rows)


def test_line_join_reaches_existing_inline_header_helpers():
    source='void f(void){helper();}'
    header='static __inline void helper(void){\n int *p;\n p=data;\n p[1]=n;\n}'
    rows=[('o','lw $t0, 0($a0)',''),('o','','lw $t0, 0($a0)')]
    result=repairs.statement_lines(source,'f',header,rows)
    assert any('p=data; p[1]=n;' in h for _,_,h in result)


def test_cast_removal_needs_matching_extra_normalization_width():
    source='int f(int x){if ((unsigned short)x<0) return 1; return (u8)x;}'
    rows=[('>','','andi $t9, $v0, 0xffff')]
    evidence=mips.extra_narrowing(rows)
    result=repairs.narrowing_casts(source,'',evidence)
    assert result and '(unsigned short)' not in result[0][1] and '(u8)' in result[0][1]
    assert not repairs.narrowing_casts(source,'',[])
    assert not repairs.narrowing_casts('int f(void){return (u16)read();}','',evidence)
    assert not mips.extra_narrowing([(' ','andi $t0,$t1,0xff','andi $t0,$t1,0xff')])
    assert not repairs.narrowing_casts('int f(int x){return (unsigned /* note */ short)x;}','',evidence)


def test_actual_extern_replaces_only_compatible_file_scope_tentative_object():
    header='typedef int BOOL;\nint Collision;\nint Other=0;\nstatic int Private;\nstruct T { int field; };\nvoid helper(void){int local;}\n'
    reference='extern BOOL Collision;\nextern int Other;\nextern int Private;\nextern int field;\nextern int local;'
    result=repairs.externs('void f(void){}',header,reference)
    assert len(result)==1 and 'extern BOOL Collision;' in result[0][2]
    for retained in ['int Other=0;','static int Private;','struct T { int field; };','void helper(void){int local;}']:
        assert retained in result[0][2]
    assert not repairs.externs('', 'int Collision;','extern float Collision;')
    assert not repairs.externs('', 'int Collision;','extern int Collision;\nextern float Collision;')


def test_missing_callee_binding_preserves_actual_prototype_and_needs_consensus():
    source='void f(void){sound(0,1.0f);}'
    header='int sound(int handle,float volume);'
    references=[(1,'#define sound func_80001234'),(2,'#define sound func_80001234')]
    result=contexts.binding_variants(source,header,'jal func_80001234',references)
    assert result and result[0][2].startswith('#define sound func_80001234\nint sound(')
    assert not contexts.binding_variants(source,header,'jal other',references)
    assert not contexts.binding_variants(source,header,'jal func_80001234',references[:1])
    assert not contexts.binding_variants('void f(void (*sound)(void)){sound();}',header,'jal func_80001234',references)


def test_private_entry_is_measured_and_only_demotes_signature_families():
    asm='lw $v0, 0x18($a0)\nmul.s $f6, $f12, $f4\njr $ra'
    rows=[('>','','mtc1 $a1, $f12')]
    assert mips.private_abi(asm,rows)['kind']=='mixed-pointer-float-entry'
    families=['parameter_width','small_helper_expansion','local_declaration_order']
    assert private_abi_order(families,'mips',asm,rows)==['small_helper_expansion','local_declaration_order','parameter_width']
    assert private_abi_order(families,'mips',asm,[])==families
    assert not mips.private_abi(asm+'\njal normal',rows)
    assert private_abi_order(families,'ppc',asm,rows)==families


def test_existing_inline_helper_can_restore_real_nonstatic_definition_line():
    source='void f(void){helper();}'
    header='static __inline void helper(void){ use(); }'
    reference='\n'*100+'void helper(void){ use(); }'
    result=contexts.helper_lines(source,header,reference)
    assert result and '#line 101 "reference.c"' in result[0][2]
    assert 'static __inline void helper' in result[0][2]
    assert not contexts.helper_lines(source,header,reference.replace('use();','other();'))


def test_retained_argument_classifies_internal_callee_context_without_new_register_binding():
    asm='or $a3, $a0, $zero\njal helper\nnop\njal other\nmove $a0, $a3\nlw $v0, 8($a3)'
    rows=[('r','move $a0, $a3','move $a0, $s0')]
    assert mips.private_abi(asm,rows)['kind']=='retained-caller-saved-argument'
    assert not mips.private_abi(asm,[])
    assert not mips.private_abi(asm+'\nlw $a3, 12($sp)',rows)


def test_mixed_float_return_delay_slot_and_displaced_integer_argument_are_classified():
    asm='sra $t9, $a0, 3\nbnez $t9, .Llater\nnop\njr $ra\nmov.s $fv0, $fa0'
    rows=[('r','sra $t9, $a0, 3','sra $t9, $a1, 3')]
    assert mips.private_abi(asm,rows)['kind']=='mixed-pointer-float-entry'
    assert not mips.private_abi(asm,[])


def test_line_join_reaches_called_inline_helper_in_the_candidate_source():
    source='static __inline void helper(int *p,int n){\n int *words;\n words=p;\n words[1]=n;\n}\nvoid f(int *p,int n){helper(p,n);}'
    rows=[('i','lw $t0, 4($sp)','lw $t1, 0($a0)'),('i','lw $t1, 0($a0)','lw $t0, 4($sp)')]
    result=repairs.statement_lines(source,'f','',rows)
    assert any('words=p; words[1]=n;' in s for _,s,_ in result)


def test_same_line_assignments_can_be_split_without_reordering_tokens():
    source='void f(void){\n a=b; c=d;\n}'
    rows=[('o','lw $t0, 0($a0)',''),('o','','lw $t0, 0($a0)')]
    assert any('a=b;\nc=d;' in s for _,s,_ in repairs.statement_lines(source,'f','',rows))
