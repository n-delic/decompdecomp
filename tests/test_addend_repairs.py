import pytest

from decompnet.app.addend_repairs import variants as addends
from decompnet.app.address_repairs import variants


def report(old=16,new=24):
    return {'rows': [("i",f'lui $v0, %hi(data+{new})',f'lui $v0, %hi(data+{old})'),
                     ("i",f'addiu $v0, $v0, %lo(data+{new})',f'addiu $v0, $v0, %lo(data+{old})')]}


@pytest.mark.parametrize('source,expected', [
    ('return data + 16;', 'return data + 24;'),
    ('return data[4];', 'return data[6];'),
    ('return data + 2;', 'return data + 3;'),
    ('return (char *)data + 16;', 'return (char *)data + 24;'),
])
def test_byte_offsets_and_typed_indices_use_evidenced_stride(source,expected):
    assert addends(source,report()) == [expected]


def test_comments_strings_and_unrelated_numbers_are_not_edited():
    source='return data[4] + 4; /* data[4] */ "data[4]";'
    assert addends(source,report()) == ['return data[6] + 4; /* data[4] */ "data[4]";']


def test_conflicts_half_pairs_and_nonintegral_offsets_are_rejected():
    r=report(); r['rows'].pop()
    assert addends('data[4]',r) == []
    r=report(); r['rows'] += report(16,32)['rows']
    assert addends('data[4]',r) == []
    assert addends('data[4]',report(16,23)) == []
    assert addends('data[3]',report()) == []


@pytest.mark.parametrize('big',[True,False])
def test_combines_scaled_addend_with_field_offset(big):
    r=report(); order='big' if big else 'little'
    r.update(candidate_target=(0x8c820008).to_bytes(4,order).hex(),candidate_relocs='[]')
    target={'target':(0x8c82000c).to_bytes(4,order),'relocs':'[]'}
    source='return data[4] + *(int *)(p + 8);'
    assert variants(source,r,target,big,{'data'}) == [
        'return data[6] + *(int *)(p + 8);', 'return data[6] + *(int *)(p + 12);']


def test_changed_base_updates_candidate_local_declaration():
    r=report();r['rows']=[(mark,a.replace('data','other'),b) for mark,a,b in r['rows']]
    assert addends('extern int data[]; return data[4];',r) == ['extern int other[]; return other[6];']


def test_octal_index_is_parsed_as_c():
    assert addends('data[010]',report(32,40)) == ['data[10]']


def test_numeric_low_half_and_signed_carry():
    r={'rows':[('i','lui $a0, %hi(D_80000000+65536)','lui $a0, %hi(D_80000000+32768)'),
               ('i','addiu $a0, $a0, -16','addiu $a0, $a0, %lo(D_80000000+32768)')]}
    assert addends('return D_80000000 + 32768;',r) == ['return D_80000000 + 65520;']


def test_numeric_low_half_requires_same_register_and_does_not_mutate_feedback():
    r={'rows':[('i','lui $a0, %hi(D_80000000+65536)','lui $a0, %hi(D_80000000+32768)'),
               ('i','addiu $a0, $a1, -16','addiu $a0, $a0, %lo(D_80000000+32768)')]}
    rows=list(r['rows'])
    assert addends('D_80000000 + 32768',r) == []
    assert r['rows'] == rows
