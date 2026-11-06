import pytest

from decompnet.app import draft_repairs, local_experiments
from decompnet.app.raw_address_repairs import variants
from decompnet.arch.mips_addresses import aligned, resolved


def asm(*words):
    return '\n'.join(f'/* {i*4:X} {0x80001000+i*4:08X} {word:08X} */ wrong %hi(D_800CFFFF)' for i,word in enumerate(words))


def test_two_independent_store_addresses_generate_singles_and_pair_only_in_function():
    source = '''extern char D_800C02AE[];
void other(void) { use(0x800C7040); }
void f(void) {
    *(int *)0x800C7040 = value;
    *(int *)(D_800C02AE + 0x684E) = 0;
    use("0x800C7040"); /* D_800C02AE + 0x684E */
}'''
    raw = asm(0x3c01800b,0xac227040,0x3c01800b,0xac206afc)
    out = variants(source,'f','mips',raw)
    assert [f for f,_ in out] == ['raw_address_correction','raw_address_correction','raw_address_pair']
    paired = out[-1][1]
    assert '*(int *)0x800b7040' in paired
    assert 'D_800C02AE - 0x97b2' in paired
    assert paired.startswith('extern char D_800C02AE[];\nvoid other(void) { use(0x800C7040); }')
    assert '"0x800C7040"' in paired and '/* D_800C02AE + 0x684E */' in paired
    assert set(draft_repairs.AGENT_FAMILIES) >= {'raw_address_correction','raw_address_pair'}
    assert set(local_experiments.FAMILIES) >= {'raw_address_correction','raw_address_pair'}
    labels={}
    assert paired in draft_repairs.variants(source,'f','mips',raw,labels=labels,limit=None)
    assert labels[paired]=='raw_address_pair'


def test_signed_low_carry_ori_byte_order_and_register_clobber():
    code=bytes.fromhex('3c02800c8c438010')
    assert resolved(code)=={4:0x800b8010}
    assert resolved(b''.join(code[i:i+4][::-1] for i in range(0,len(code),4)),False)=={4:0x800b8010}
    assert resolved(bytes.fromhex('3c02800b3442ffffac430000'))=={4:0x800bffff,8:0x800bffff}
    for word in ('00401021','0c001000','10430000','04410000','38000000'):
        assert not resolved(bytes.fromhex('3c02800c'+word+'8c438010'))
    assert not resolved(b'\0')


def test_ambiguous_already_valid_and_non_mips_candidates_stay_untouched():
    source='void f(void) { use(*(int *)0x800C7040); }'
    raw=asm(0x3c01800b,0xac227040,0x3c01800d,0xac227040)
    assert not variants(source,'f','mips',raw)
    assert not variants(source,'f','ppc',asm(0x3c01800b,0xac227040))
    assert not variants(source,'f','mips',asm(0x3c01800c,0xac227040))


@pytest.mark.parametrize('decl',['extern int D_800C02AE[];','extern int *D_800C02AE;'])
def test_compound_pointer_scaling_is_not_guessed(decl):
    assert not variants(decl+'\nvoid f(void) { *(int *)(D_800C02AE+0x684E)=0; }','f','mips',asm(0x3c01800b,0xac206afc))


def test_scalar_use_is_local_not_a_shared_symbol_rename():
    source='extern volatile int D_800C7040;\nvoid f(void) { use(D_800C7040); }'
    out=variants(source,'f','mips',asm(0x3c01800b,0xac227040))
    assert out==[('raw_address_correction','extern volatile int D_800C7040;\nvoid f(void) { use((*(volatile int *)0x800b7040)); }')]
    assert not variants('void f(void) { int D_800C7040; use(D_800C7040); }','f','mips',asm(0x3c01800b,0xac227040))


def test_macro_stringification_is_not_rewritten():
    source='void f(void) { CAPTURE(*(int *)0x800C7040); }'
    assert not variants(source,'f','mips',asm(0x3c01800b,0xac227040),'#define CAPTURE(x) puts(#x)')


def test_aligned_numeric_annotations_do_not_change_rows_and_reject_wrong_lengths():
    rows=[('i','lui $at, %hi(misleading)','lui $at, %hi(other)'),('i','sw $v0, %lo(x)($at)','sw $v0, 0x7040($at)')]
    a=bytes.fromhex('3c01800bac227040');b=bytes.fromhex('3c01800cac227040')
    assert aligned(rows,a,b)==[{'row':1,'target':0x800b7040,'candidate':0x800c7040}]
    assert rows[1][1]=='sw $v0, %lo(x)($at)'
    assert not aligned(rows,a,b+b'\0'*4)


def test_missing_raw_instruction_breaks_tracking():
    from decompnet.arch.mips_addresses import addresses
    assert not addresses('/* 0 80001000 3C01800B */ lui\n/* 8 80001008 AC227040 */ sw')


def test_cached_autopilot_symbolic_feedback_can_still_propose_raw_pair():
    from decompnet.app.address_repairs import variants as guided
    source='extern char D_800C02AE[];\nvoid f(void) { *(int *)0x800C7040=1; *(int *)(D_800C02AE+0x684E)=0; }'
    target={'name':'f','asm':asm(0x3c01800b,0xac227040,0x3c01800b,0xac206afc)}
    proposals=guided(source,{'rows':[]},target,True,set())
    assert len(proposals)==3
    assert '0x800b7040' in proposals[2] and 'D_800C02AE - 0x97b2' in proposals[2]
