import struct

from decompnet.app import reference_stage_repairs as stages
from decompnet.app import retail_context_repairs as retail
from decompnet.app import scalar_pool_recovery as pools
from decompnet.arch import mips_pointer_lifetime as lifetimes
from decompnet.arch.mips_source_recovery import private_abi


def test_actual_wrapper_is_private_inline_only_when_absent_from_target_calls():
    source='int entry(int *p){return wrapper(p);}'
    header='int wrapper(int *p);\nint lookup(int *p);'
    reference='\n'*12+'int wrapper(int *p){int x; x=lookup(p); return x;}\n'+source
    result=stages.inline_helpers(source,header,reference,['lookup'])
    assert len(result)==1 and 'static __inline int wrapper' in result[0][2]
    assert '#line 13 "reference.c"' in result[0][2]
    assert not stages.inline_helpers(source,header,reference,['wrapper'])
    assert not stages.inline_helpers(source,header,reference,[])
    assert not stages.inline_helpers(source,header,reference.replace('lookup(p)','wrapper(p)'),['wrapper'])


def test_existing_callback_or_helper_is_not_replaced():
    source='int entry(int *p){return wrapper(p);}'
    reference='int wrapper(int *p){return lookup(p);}'
    assert not stages.inline_helpers(source,'int (*wrapper)(int *);',reference,['lookup'])
    assert not stages.inline_helpers(source,'static int wrapper(int *p){return 1;}',reference,['lookup'])


def test_original_complete_declaration_order_and_register_hint():
    source='void entry(void){ int *p; int n; register float q; float a[3], b[3]; work(); }'
    reference='void entry(void){ int n; int *p; float a[3], b[3]; float q; work(); }'
    result=stages.declarations(source,'entry',reference)
    assert len(result)==1 and result[0][1]==reference
    assert not stages.declarations(source,'entry',reference.replace('int n;','short n;'))
    assert not stages.declarations(source,'entry',reference.replace('int n;','int n=side_effect();'))
    assert not stages.declarations(source,'entry',reference.replace('int n;','int n; int extra;'))
    assert not stages.declarations(source,'entry',source)


def test_wrapper_and_declaration_hypotheses_compose():
    source='int entry(void){int b; int a; return wrapper();}'
    reference='int entry(void){int a; int b; return wrapper();}\nint wrapper(void){return leaf();}'
    result=stages.stages(source,'entry','int wrapper(void);int leaf(void);',reference,['leaf'])
    assert any('int a; int b;' in s and 'static __inline int wrapper' in h for _,s,h in result)


def test_real_callee_body_is_available_for_clobber_context():
    result=stages.inline_helpers('int f(void){return leaf();}','int leaf(void);',
                                 'int leaf(void){return dependency();}',['leaf'],clobbers=True)
    assert len(result)==1 and 'static int leaf' in result[0][2]
    assert '__inline' not in result[0][2]


def test_macro_arithmetic_and_string_references_can_leave_one_pool():
    source='void f(void){use("clip"); use(0.01); use(SCALE*0.3*frame);}'
    asm='addiu $a0,$a0,%lo(D_80002000)\nldc1 $f2,%lo(D_80001000)($at)\nldc1 $f4,%lo(D_80001008)($at)'
    memory={0x80002000:b'clip\0',0x80001000:struct.pack('>d',0.01),0x80001008:struct.pack('>d',0.0375)}
    read=lambda a,n:memory.get(a,b'')[:n]
    result=pools.variants(source,'#define SCALE 0.125\n',asm,read,lambda s:int(s[2:],16))
    assert any('use(D_80002000)' in s and 'use(0.01)' in s and 'D_80001008*frame' in s for _,s,_ in result)
    assert all('extern const char D_80002000[];' in s for _,s,_ in result if 'use(D_80002000)' in s)


def test_strings_require_unique_complete_target_bytes_and_skip_directives():
    source='#line 1 "clip"\nvoid f(void){use("clip");}'
    asm='addiu $a0,$a0,%lo(D_80001000)'
    read=lambda a,n:b'clip\0'[:n]
    result=pools.strings(source,'',asm,read,lambda s:0x80001000)
    assert len(result)==1 and '#line 1 "clip"' in result[0][1]
    assert not pools.strings(source,'',asm,lambda a,n:b'clips',lambda s:0x80001000)
    assert not pools.strings(source,'',asm+'\naddiu $a1,$a1,%lo(D_80002000)',read,lambda s:int(s[2:],16))
    assert not pools.strings('void f(void){use("cl" "ip");}','',asm,read,lambda s:0x80001000)
    assert not pools.strings('void f(void){/* use("clip"); */}','',asm,read,lambda s:0x80001000)


POINTER_ASM='''lw $t0, 0x10($a0)
sw $t0, 0x24($sp)
sw $zero, 0x10($a0)
jal step
nop
lw $t1, 0x24($sp)
jal step
sw $t1, 0x10($s0)
sw $zero, 0x10($s0)
'''


def test_pointer_evidence_and_source_mapping_restore_only_second_call():
    proof=lifetimes.read(POINTER_ASM)
    assert proof==[{'offset':16,'callee':'step','ordinal':2}]
    source='void f(Obj *p){\n int n;\n p->matrix=0;\n step(p);\n step(p);\n}'
    header='typedef struct { int a,b,c,d; Mtx *matrix; } Obj;'
    result=retail.pointer_lifetimes(source,'f',header,proof,[(' ','','sw $zero, 0x10($a0)')])
    assert len(result)==1
    s=result[0][1]
    assert 'Mtx *cpu_saved_matrix;' in s and s.count('p->matrix = cpu_saved_matrix;')==1
    assert s.index('step(p);')<s.index('p->matrix = cpu_saved_matrix;')
    assert not retail.pointer_lifetimes(source,'f',header,proof,[(' ','','sw $zero, 0x14($a0)')])
    assert not retail.pointer_lifetimes(source.replace('step(p);','if (n) step(p);'),'f',header,proof,[(' ','','sw $zero, 0x10($a0)')])


def test_pointer_evidence_rejects_overwritten_saved_value_and_missing_clear():
    assert not lifetimes.read(POINTER_ASM.replace('jal step\nsw $t1','move $t1, $a1\njal step\nsw $t1'))
    assert not lifetimes.read(POINTER_ASM.removesuffix('sw $zero, 0x10($s0)\n'))


def test_explicit_commented_condition_needs_real_symbols_and_declared_names():
    source='void f(void){\n// if (flags & MASK)\n// table[INDEX] = on;\n// else\n// table[INDEX] = off;\n}'
    header='int flags; enum{INDEX}; char *table[1];\n#define MASK 4\n#define on D_80001000\n#define off D_80002000\n'
    result=retail.commented_conditions(source,header,['D_80001000','D_80002000'])
    assert len(result)==1 and '// if' not in result[0][1]
    assert not retail.commented_conditions(source,header,['D_80001000'])
    assert not retail.commented_conditions(source,header.replace('enum{INDEX};',''),['D_80001000','D_80002000'])


def test_private_abi_requires_live_temporary_without_spill_or_redefinition():
    asm='move $t0, $a1\njal actual\nnop\nlw $v0, 4($t0)'
    assert private_abi(asm,[('r','x','y')])['kind']=='retained-caller-saved-temporary'
    assert private_abi(asm.replace('jal actual','sw $t0, 8($sp)\njal actual'),[('r','x','y')]) is None
    assert private_abi(asm.replace('nop','move $t0, $v0'),[('r','x','y')]) is None
    assert private_abi(asm,[]) is None


def test_array_index_needs_two_measured_references_and_actual_target_base():
    source='void f(void){D_80001000[3]=value;use(D_80001000[3]);}'
    header='extern float D_80001000[4];'
    rows=[('i','lui $at,%hi(D_80001000)','lui $at,%hi(D_8000100C)'),('i','lwc1 $f2,%lo(D_80001000)($at)','lwc1 $f2,%lo(D_8000100C)($at)')]
    assert retail.array_indices(source,header,rows,lambda s:0x80001000)[0][1].count('[0]')==2
    assert not retail.array_indices(source,header,rows[:1],lambda s:0x80001000)
    assert not retail.array_indices(source,header,rows,lambda s:None)
    assert not retail.array_indices(source,header.replace('float','double'),rows,lambda s:0x80001000)


def test_later_cleanup_clears_do_not_confuse_entry_saved_pointer():
    source='void f(Obj *p){\n int n;\n p->matrix=0;\n step(p);\n step(p);\n if (n) p->other=0;\n}'
    header='typedef struct { int a,b,c,d; Mtx *matrix; Mtx *other; } Obj;'
    rows=[(' ','','sw $zero, 0x10($a0)')]
    assert retail.pointer_lifetimes(source,'f',header,lifetimes.read(POINTER_ASM),rows)
    assert not retail.pointer_lifetimes(source.replace('p->matrix=0;','p->matrix=0; p->other=0;'),'f',header,lifetimes.read(POINTER_ASM),rows)


def test_two_actual_wrappers_form_one_coherent_private_context():
    source='int f(int n){return wrap_a(n)+wrap_b(n);}'
    header='int wrap_a(int n); int wrap_b(int n); int leaf(int n);'
    reference='int wrap_a(int n){return leaf(n);}\nint wrap_b(int n){return leaf(n+1);}'
    result=stages.inline_helpers(source,header,reference,['leaf'])
    assert len(result)==2
    assert 'static __inline int wrap_a' in result[-1][2]
    assert 'static __inline int wrap_b' in result[-1][2]


def test_missing_prefix_reuses_supplied_draft_and_param_bindings_only():
    source='void f(Obj *p, App *app){int n;\n original(app);}'
    draft='#define FLAG(p) (*(int*)(p))\nint check(void *p);\nvoid f(void *a, void *b){void *saved; saved=b; if(FLAG(saved)){if(check(saved)) FLAG(saved)=0;} original(b);}'
    rows=[('<','jal check',''),(' ','jal original','jal original')]
    result=retail.draft_prefix(source,'f','',draft,rows,'mips')
    assert len(result)==1
    assert 'saved=app;' in result[0][1] and '#define FLAG' in result[0][2]
    assert result[0][1].index('int n;')<result[0][1].index('saved=app;')
    assert not retail.draft_prefix(source,'f','',draft,[(' ','jal original','jal original')],'mips')
    assert not retail.draft_prefix(source,'f','',draft.replace('if(check(saved))','if(unknown(saved))'),rows,'mips')
    assert not retail.draft_prefix(source,'f','',draft.replace('saved=b','saved=side_effect(b)'),rows,'mips')


def test_actual_getter_replaces_only_a_single_line_function_macro():
    source='int f(Obj *p){return getter(p);}'
    header='typedef struct {int flag;} Obj;\n#define ACCESS(p) (p)\n#define getter(p) (ACCESS(p)->flag)\n'
    reference='int getter(Obj *p){return ACCESS(p)->flag;}'
    result=stages.inline_helpers(source,header,reference,[])
    assert len(result)==1
    assert 'static __inline int getter' in result[0][2]
    assert '#define getter' not in result[0][2] and '#define ACCESS' in result[0][2]
    assert not stages.inline_helpers(source,header,reference.replace('return ACCESS(p)->flag;','p->flag=1; return p->flag;'),[])
    assert not stages.inline_helpers(source,'#define getter actual\n',reference,[])


def test_scalar_pools_keep_signed_value_and_offer_per_use_precision():
    source='void f(void){use(-PI);x=LIMIT; if(x<LIMIT)use(1);}'
    header='#define PI 3.0\n#define LIMIT 0.4\n'
    memory={0x80001000:struct.pack('>d',-3.0),0x80001008:struct.pack('>f',0.4),0x80001010:struct.pack('>d',0.4)}
    asm='ldc1 $f2,%lo(D_80001000)($at)\nlwc1 $f4,%lo(D_80001008)($at)\nldc1 $f6,%lo(D_80001010)($at)'
    result=pools.variants(source,header,asm,lambda a,n:memory.get(a,b'')[:n],lambda s:int(s[2:],16))
    assert any('use(D_80001000);x=D_80001008; if(x<D_80001010)' in s for _,s,_ in result)
    assert all('-D_80001000' not in s for _,s,_ in result)
    assert len(result)<=8
