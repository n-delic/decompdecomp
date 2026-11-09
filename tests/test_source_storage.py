from decompnet.app.source_definitions import extract
from decompnet.app.source_storage import object_hints, scope_hints, wide_argument_hints


def test_original_file_static_is_preserved_with_initializer_and_line_breaks():
    source='\nstatic unsigned long long timer\n = 7ULL;\n'
    output=extract(source,'timer')
    assert source.strip() in output and 'static' in output


def test_local_static_scope_loss_is_flagged_without_hoisting_or_rewriting():
    original='int f(void){\n static int *cache=0;\n return *cache;\n}'
    changed='int f(void){return *cache;}'
    assert not scope_hints(original,original,'','f')
    hints=scope_hints(original,changed,'extern int *cache;','f')
    assert len(hints)==1 and 'Local static cache' in hints[0] and 'No declaration was moved' in hints[0]


def test_extra_wide_address_loads_suggest_original_definitions_not_invented_zeroes():
    rows=[[' ','lui $at, %hi(D_80100000)','lui $at, %hi(D_80100000)'],
          ['>','','lui $at, %hi(D_80100004)']]
    hint=object_hints(rows,'extern u64 D_80100000;')[0]
    assert '2 address-high loads versus 1' in hint and 'not permission to invent zero' in hint
    assert not object_hints(rows,'u64 D_80100000;')
    assert not object_hints(rows,'extern int D_80100000;')


def test_wide_argument_evidence_respects_narrow_wide_and_separate_scalar_declarations():
    asm='lw $a0, 0x38($s0)\nlw $a2, 0($t0)\njal send\nlw $a3, 4($t0)'
    hints=wide_argument_hints(asm,'int send(void *buf, u32 size);')
    assert len(hints)==1 and '$a2/$a3' in hints[0] and 'no width was inferred' in hints[0]
    assert not wide_argument_hints(asm,'int send(void *buf, u64 size);')
    assert not wide_argument_hints(asm,'int send(void *buf, u32 first, u32 second);')
    assert not wide_argument_hints(asm.replace('4($t0)','8($t0)'),'int send(void*, u32);')
    assert not wide_argument_hints(asm.replace('jal send','move $a2, $zero\njal send'),'int send(void*, u32);')
