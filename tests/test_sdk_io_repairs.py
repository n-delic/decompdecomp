from decompnet.app import draft_repairs, profile_hypotheses, sdk_io_repairs

ASM='''/* 0 80001000 3c08a450 */ lui $t0, %hi(D_A4500010)
/* 4 80001004 ad090010 */ sw $t1, %lo(D_A4500010)($t0)
/* 8 80001008 3c0aa460 */ lui $t2, %hi(D_A4600010)
/* c 8000100c 8d500010 */ lw $s0, %lo(D_A4600010)($t2)
/* 10 80001010 afa90018 */ sw $t1, 0x18($sp)
'''


def test_mmio_requires_raw_target_address_width_and_volatile_declaration():
    header='extern volatile u32 D_A4500010;'
    src='void f(void){D_A4500010=1;}'
    candidates=sdk_io_repairs.variants(src,'f','mips',ASM,header)
    assert any('(*(volatile u32 *)0xA4500010)=1' in text for _,text in candidates)
    assert not sdk_io_repairs.variants(src,'f','mips','lui $t0, %hi(D_A4500010)',header)
    assert not sdk_io_repairs.variants(src,'f','mips',ASM,header.replace('u32','u16'))
    assert not sdk_io_repairs.variants(src,'f','ppc',ASM,header)
    assert not sdk_io_repairs.variants(src,'f','mips',ASM,header.replace('volatile ',''))
    assert not sdk_io_repairs.variants('void f(u32 D_A4500010){D_A4500010=1;}','f','mips',ASM,header)
    # Numeric-looking target symbol alone is not proof; actual word encodes another address.
    assert not sdk_io_repairs.variants(src,'f','mips',ASM.replace('ad090010','ad090014'),header)


def test_address_forms_and_opacity():
    src='void f(void){ *(volatile u32 *)&D_A4500010=1; /* D_A4500010 */ }'
    got=sdk_io_repairs.variants(src,'f','mips',ASM,'extern volatile u32 D_A4500010;')
    assert any('*)0xA4500010)' in text for _,text in got)
    assert all('/* D_A4500010 */' in text for _,text in got)
    src='void f(void){*(volatile u32 *)0xA4500010=1;}'
    assert any('D_A4500010=1' in text for _,text in sdk_io_repairs.variants(src,'f','mips',ASM,'extern volatile u32 D_A4500010;'))
    assert sdk_io_repairs.accesses(ASM.replace('3c08a450','3c088000'))[0][0]==0xa4600010


def test_local_qualifiers_are_bounded_and_never_strip_hardware_volatile():
    src='void f(void){u32 status;status=IO_READ(0xa4600010);while(status&2){status=IO_READ(0xa4600010);}}'
    variants=sdk_io_repairs.variants(src,'f','mips',ASM)
    assert any('register u32 status;' in s for _,s in variants)
    assert any('volatile u32 status;' in s for _,s in variants)
    assert not sdk_io_repairs.variants(src.replace('status=IO_READ','sink(&status);status=IO_READ',1),'f','mips',ASM)
    src='void f(void){volatile u32 status;status=*(volatile u32*)0xa4600010;}'
    got=sdk_io_repairs.variants(src,'f','mips',ASM)
    assert any('u32 status;' in s and '*(volatile u32*)0xa4600010' in s for _,s in got)


def test_byte_record_copy_uses_existing_byte_fields_and_target_unaligned_loads():
    asm=ASM+'/* 14 80001014 89090000 */ lwl $t1,0($t0)\n/* 18 80001018 99090003 */ lwr $t1,3($t0)\n'
    header='typedef struct {u8 first;u8 second;u8 third;u8 fourth;} Reply;'
    src='void f(u8 *p){Reply reply;reply.first=p[0];reply.second=p[1];reply.third=p[2];reply.fourth=p[3];}'
    assert any('reply = *(Reply *)p;' in s for _,s in sdk_io_repairs.variants(src,'f','mips',asm,header))
    assert not sdk_io_repairs.variants(src,'f','mips',ASM,header)
    assert not sdk_io_repairs.variants(src,'f','mips',asm,header.replace('u8 second','u32 second'))


def test_io_profile_and_diff_advisories_do_not_assert_identity_or_edit_constants():
    proj={'arch':'mips'};own={'toolchain':'ido-5.3','flags':['-O2','-mips2']}
    assert profile_hypotheses.prioritized(proj,{'asm':ASM},own)==[['-O1','-mips2']]
    assert not profile_hypotheses.prioritized(proj,{'asm':'','name':'osFamiliarName'},own)
    assert not profile_hypotheses.prioritized({'arch':'arm'},{'asm':ASM},own)
    rows=[['i','addiu $t3, $zero, 75','addiu $t3, $zero, 4'],['>','','beqz $t0, .L80']]
    hints=sdk_io_repairs.hints('void f(void){}',ASM,rows)
    assert any('target 75, candidate 4' in h for h in hints)
    assert any('recovery paths' in h for h in hints)
    assert not sdk_io_repairs.hints('void f(void){}','',rows)
    labels={};source='void f(void){D_A4500010=1;}'
    got=draft_repairs.variants(source,'f','mips',ASM,header='extern volatile u32 D_A4500010;',labels=labels)
    assert any(labels[s]=='proven_mmio_forms' for s in got)
