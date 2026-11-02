from decompnet.app import independent_address_repairs as repair
from decompnet.arch.mips_materializations import addresses


def evidence():
    return [(' ','lui $a0, 0x8000','lui $a0, 0x8000'),
            (' ','addiu $a0, $a0, 0x4100','addiu $a0, $a0, 0x4100'),
            ('<','lui $v0, 0x8000',''),
            ('i','addiu $v0, $v0, 0x4100','addiu $v0, $a1, 0x100')]


def source():
    return 'typedef unsigned char u8;extern int D_80004000;int f(void){u8 *base;base=(u8*)(&D_80004000);return (int)(base+0x100);}'


def test_repeated_target_address_gets_one_independent_resolved_alias():
    result=repair.variants(source(),'f','mips',evidence())
    assert len(result)==1
    assert result[0][0]=='independent_address_materialization'
    assert 'extern char RO_80004100;' in result[0][1]
    assert '(int)(&RO_80004100)' in result[0][1]
    assert not repair.variants(source(),'f','ppc',evidence())
    assert not repair.variants(source(),'f','mips',evidence()[:2])
    assert not repair.variants(source(),'f','mips',[(m,t,t) for m,t,c in evidence()])


def test_uncertain_pointer_writes_escapes_and_control_flow_are_rejected():
    for added in ['base++;','base+=2;','use(&base);','if(ready){base=0;}','goto jump;jump:']:
        text=source().replace('return (int)',added+'return (int)')
        assert not repair.variants(text,'f','mips',evidence())
    assert not repair.variants(source(),'f','mips',evidence(),'#define base hidden')
    assert repair.variants(source().replace('return (int)','if(NULL){}return (int)'),'f','mips',evidence(),'#define NULL ((void*)0)')==[]  # block boundary is uncertain


def test_address_pair_signed_carry_symbolic_identity_and_register_checks():
    rows=[(' ','lui $a0, 0x8001',''),(' ','addiu $a0, $a0, -1','')]
    assert addresses(rows,1)[0x8000ffff]==1
    assert addresses([(' ','lui $a0, %hi(D_80004100)',''),(' ','addiu $a0, $a0, %lo(D_80004100)','')],1)[0x80004100]==1
    assert not addresses([(' ','lui $a0, 0x8000',''),(' ','addiu $v0, $a0, 0x4100','')],1)


def test_instruction_evidence_never_accepts_a_match_or_edits_a_shared_header():
    header='typedef int shared_type;'
    candidates=repair.variants(source(),'f','mips',evidence(),header)
    assert candidates
    assert header=='typedef int shared_type;'
    assert 'base=(u8*)(&D_80004000)' in candidates[0][1]
