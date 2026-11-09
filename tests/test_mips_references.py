import pytest

from decompnet.arch.mips_references import references


@pytest.mark.parametrize('order',['big','little'])
def test_signed_address_materialization_and_direct_calls_include_delay_slots(order):
    words=[0x3c048011,0x2484ffe0,0x0c040000,0x3c058010,0x24a50010]
    image=b''.join(w.to_bytes(4,order) for w in words)
    result=list(references(image,0,len(image),0x80001000,big_endian=order=='big'))
    assert result==[(0x8010ffe0,4,'address'),(0x80100000,8,'call')]


def test_an_overwritten_upper_register_does_not_make_a_pointer():
    words=[0x3c048010,0x8c840000,0x24840010]
    image=b''.join(w.to_bytes(4,'big') for w in words)
    assert not list(references(image,0,len(image),0x80001000))


def test_branch_join_does_not_inherit_an_upper_from_the_fallthrough_path():
    words=[0x10000002,0,0x3c048010,0x24840010]
    image=b''.join(w.to_bytes(4,'big') for w in words)
    assert list(references(image,0,len(image),0x80001000))==[(0x8000100c,0,'branch')]


def test_address_completed_in_a_call_delay_slot_is_retained():
    words=[0x3c048010,0x0c040000,0x24840010]
    image=b''.join(w.to_bytes(4,'big') for w in words)
    assert list(references(image,0,len(image),0x80001000))==[(0x80100000,4,'call'),(0x80100010,8,'address')]
