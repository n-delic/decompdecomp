from decompnet.app.source_context_diagnostics import identity, route, shadows
from decomprepair.typed_draft_repairs import variants


def test_shadowed_implicit_int_is_advisory():
    assert shadows('void f(void) { float angle; if (x) { register angle = -x; } }')
    assert not shadows('void f(void) { float angle; register float other = -angle; }')


def test_disjoint_identity_needs_positive_calls_and_fields():
    c={'target':{'calls':['a','b'],'offsets':[4,8]},'candidate':{'calls':['c','d'],'offsets':[16,20]}}
    assert identity(c)
    c['candidate']['calls']=['a','d']
    assert not identity(c)
    assert not identity({})


def test_epilogue_route_keeps_boundaries():
    rows=[(' ','jr $ra','jr $ra'),(' ','nop','nop'),('>','','lw $ra, 0x10($sp)'),('>','','jr $ra'),('>','','addiu $sp, $sp, 0x18')]
    assert route('',rows,'mips')['kind']=='epilogue-context'
    assert not route('',rows,'x86')


HEADER='''typedef struct { int mode; short status; unsigned char pad[2]; float alpha; } App;
#define W(p,o) (*(int *) ((u8 *) p + o))
#define H(p,o) (*(short *) ((u8 *) p + o))
#define F(p,o) (*(float *) ((u8 *) p + o))
'''


def test_typed_fields_use_actual_layout_and_macro_body():
    source='void f(App *p) { if (W(p,0)) { H(p,4)=32; F(p,8)=0; } }'
    pairs=variants(source,'f',HEADER)
    assert len(pairs)==1
    assert 'p->mode' in pairs[0][1] and 'p->status=32' in pairs[0][1] and 'p->alpha=0' in pairs[0][1]
    assert pairs[0][2]==HEADER
    assert not variants(source,'f',HEADER.replace('int mode','Unknown mode'))
    assert not variants(source,'f',HEADER.replace('((u8 *) p + o)','((u8 *) p - o)'))
    assert not variants(source,'f','#pragma pack(1)\n'+HEADER)
    assert not variants(source,'f',HEADER.replace('int mode','volatile int mode'))


def test_fields_reject_wrong_width_and_untyped_pointer():
    assert not variants('void f(App *p) { H(p,0)=1; }','f',HEADER)
    assert not variants('void f(void *p) { W(p,0)=1; }','f',HEADER)


def test_shadowed_pointer_is_not_assumed_to_have_parameter_layout():
    assert not variants('void f(App *p) { { void *p; W(p,0)=1; } }','f',HEADER)


def test_private_temporary_entry_requires_measured_load_and_no_definition():
    from decompnet.arch.mips_source_recovery import private_abi
    asm='glabel f\n/* 0 0 0 */ lw $v0, 0x8($t2)\n/* 4 4 0 */ jr $ra\n/* 8 8 0 */ nop'
    rows=[('r','lw $v0, 0x8($t2)','lw $v0, 0x8($a0)')]
    assert private_abi(asm,rows)['kind']=='temporary-register-entry'
    assert not private_abi(asm.replace('glabel f','glabel f\n/* 0 0 0 */ or $t2, $a0, $zero'),rows)
    assert not private_abi(asm,[])


def test_rom_literal_identity_needs_multiple_complete_strings():
    from decompnet.app.source_context_diagnostics import strings
    asm='lui $t0, %lo(D_80001000)\nlui $t0, %lo(D_80002000)'
    data={0x80001000:b'Controller missing\0',0x80002000:b'Insert cartridge\0'}
    read=lambda a,n:data[a]+b'\0'*256
    resolve=lambda n:int(n[2:],16)
    assert strings('puts("Gallery title"); puts("Developer credit");',asm,read,resolve)
    assert not strings('puts("Controller missing"); puts("Developer credit");',asm,read,resolve)
    assert not strings('puts("One string");',asm,read,resolve)
