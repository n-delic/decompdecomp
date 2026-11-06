import pytest

from decompnet.app.argument_repairs import variants
from decompnet.arch.mips_arguments import evidence


def fixture(old,new,big=True,name='wrapper'):
    order='big' if big else 'little'
    left=b''.join(w.to_bytes(4,order) for w in old)
    target={'target':b''.join(w.to_bytes(4,order) for w in new),'relocs':'[]','name':name}
    return {'candidate_target':left.hex(),'candidate_relocs':'[]'},target


@pytest.mark.parametrize('big',[True,False])
@pytest.mark.parametrize('delta',[1,2])
def test_wrappers_forward_existing_arguments_and_fix_only_local_prototype(big,delta):
    old=[0x27bdffe8,0xafbf0010,0x0c000800,0x24040004,0x8fbf0010,0x03e00008,0x27bd0018]
    new=[*old];new[3]=0x24040004+(delta<<16)
    report,target=fixture(old,new,big)
    source='M2C_UNK callee(M2C_UNK); /* extern */\nvoid wrapper(void) {\n    callee(4);\n}\n'
    result=variants(source,report,target,big)
    params=', '.join(f'int __reuse_arg{i}' for i in range(delta))
    types=', '.join(['int']*delta+['M2C_UNK'])
    args=', '.join([f'__reuse_arg{i}' for i in range(delta)]+['4'])
    assert result==[f'M2C_UNK callee({types}); /* extern */\nvoid wrapper({params}) {{\n    callee({args});\n}}\n']


@pytest.mark.parametrize('big',[True,False])
def test_missing_incoming_parameter_shifts_the_live_pointer(big):
    report,target=fixture([0x00808021,0x03e00008,0],[0x00a08021,0x03e00008,0],big)
    source='void wrapper(void *arg1) { use(arg1); } /* wrapper(void *arg1) */'
    assert variants(source,report,target,big)==['void wrapper(int __reuse_arg0, void *arg1) { use(arg1); } /* wrapper(void *arg1) */']


def test_incoming_gap_preserves_parameters_before_the_affected_one():
    report,target=fixture([0x00a01021],[0x00c01021])
    assert variants('int wrapper(int first, int third) { return third; }',report,target,True)==[
        'int wrapper(int first, int __reuse_arg0, int third) { return third; }']


def test_load_base_can_show_a_missing_incoming_pointer():
    report,target=fixture([0x8c820010],[0x8ca20010])
    assert variants('int wrapper(void *p) { return *(int *)p; }',report,target,True)==[
        'int wrapper(int __reuse_arg0, void *p) { return *(int *)p; }']


@pytest.mark.parametrize('parameters',['float x','double x','long long x','s64 x','struct Thing x','int x, ...','int x[4]'])
def test_nonordinary_or_aggregate_arguments_are_left_alone(parameters):
    report,target=fixture([0x00808021],[0x00a08021])
    assert variants(f'void wrapper({parameters}) {{ use(x); }}',report,target,True)==[]


def test_prior_register_write_and_prior_call_reject_incoming_evidence():
    for prefix in [0x24040001,0x24050001,0x0c000800,0x10000001,0x44042000]:
        report,target=fixture([prefix,0x00808021],[prefix,0x00a08021])
        assert variants('void wrapper(void *p) { use(p); }',report,target,True)==[]


def test_conflicting_deltas_nonargument_registers_and_changed_immediates_are_rejected():
    for old,new in [([0x24040001],[0x24080001]),([0x24040001],[0x24050002]),
                    ([0x00808021,0x00808821],[0x00a08021,0x00c08821])]:
        report,target=fixture(old,new)
        assert variants('void wrapper(int x) { use(x); }',report,target,True)==[]


def test_complex_wrappers_recursion_and_generated_name_collisions_are_rejected():
    report,target=fixture([0x0c000800,0x24040000],[0x0c000800,0x24050000])
    for body in ['callee(0); other();','if (x) callee(0);','wrapper(0);','callee(other());']:
        assert variants(f'void wrapper(void) {{ {body} }}',report,target,True)==[]
    assert variants('int __reuse_arg0; void wrapper(void) { callee(0); }',report,target,True)==[]


def test_comments_strings_and_unspecified_callee_declarations_are_preserved():
    report,target=fixture([0x0c000800,0x24040000],[0x0c000800,0x24050000])
    source='int callee(); /* callee(0); */\nvoid wrapper(void) { callee(0); }'
    assert variants(source,report,target,True)==['int callee(); /* callee(0); */\nvoid wrapper(int __reuse_arg0) { callee(__reuse_arg0, 0); }']


def test_relocations_empty_or_unaligned_inputs_cannot_teach_register_positions():
    assert evidence(b'',b'',set(),True) is None
    assert evidence(b'a',b'b',set(),False) is None
    report,target=fixture([0x00808021],[0x00a08021]);report['candidate_relocs']='[[0,5,0]]'
    assert variants('void wrapper(int x) { use(x); }',report,target,True)==[]



def test_named_local_callee_prototypes_are_adjusted():
    report,target=fixture([0x0c000800,0x24040000],[0x0c000800,0x24050000])
    source='int callee(int value);\nvoid wrapper(void) { callee(0); }'
    assert variants(source,report,target,True)==['int callee(int, int value);\nvoid wrapper(int __reuse_arg0) { callee(__reuse_arg0, 0); }']


def test_relocated_call_opcode_changes_are_not_treated_as_argument_only():
    report,target=fixture([0x0c000800,0x24040000],[0x08000800,0x24050000])
    report['candidate_relocs']='[[0,4,0]]'
    assert variants('void wrapper(void) { callee(0); }',report,target,True)==[]


def test_signature_repair_combines_with_candidate_local_address_correction():
    from decompnet.app.address_repairs import variants as all_repairs
    report,target=fixture([0x00808021],[0x00a08021])
    report['rows']=[('i','lui $v0, %hi(D_80002000)','lui $v0, %hi(D_80001000)'),
                    ('i','lw $v0, %lo(D_80002000)($v0)','lw $v0, %lo(D_80001000)($v0)')]
    source='extern int D_80001000;\nvoid wrapper(void *p) { use(p, D_80001000); }'
    assert all_repairs(source,report,target,True,set())==[
        'extern int D_80002000;\nvoid wrapper(void *p) { use(p, D_80002000); }',
        'extern int D_80002000;\nvoid wrapper(int __reuse_arg0, void *p) { use(p, D_80002000); }']
