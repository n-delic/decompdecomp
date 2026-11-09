"""Loader constants, not coincidental library calls, establish bank boundaries."""
import pytest

from decompnet.consoles.n64.loader_mapping import refine, transfers


def const(reg,value):
    return [0x3c000000|(reg<<16)|(value>>16),0x34000000|(reg<<21)|(reg<<16)|(value&0xffff)]


def fixture(count=3,second=True):
    words=[];ranges=[]
    for i in range(count):
        lo=0x3000+i*0x100;ram=0x80100000+i*0x200
        ranges.append((lo,lo+0x100,ram))
        words+=const(16,lo)+const(17,lo+0x100)
        words+=[(17<<21)|(16<<16)|(17<<11)|35]  # subu s1,s1,s0
        words+=[(16<<21)|(5<<11)|33]+const(6,ram)
        words+=[0x0c000800,(17<<21)|(7<<11)|33]  # delay: a3 = length
        if second:
            words+=[(16<<21)|(4<<11)|33]+const(5,ram)
            words+=[0x0c000900,(17<<21)|(6<<11)|33]
    image=bytearray(0x4000)
    for i,w in enumerate(words):image[0x1000+i*4:0x1004+i*4]=w.to_bytes(4,'big')
    return bytes(image),[(0x1000,0x1000+len(words)*4,0x80001000)],ranges


def test_independent_loader_and_cache_calls_agree_on_adjacent_banks():
    image,taken,ranges=fixture()
    assert transfers(image,taken)==ranges
    # One coarse statistical segment must split at the actual loader boundaries.
    assert refine(image,[(0x3020,0x32e0,0x80010000)],taken)==[
        (0x3020,0x3100,0x80100020),(0x3100,0x3200,0x80100200),(0x3200,0x32e0,0x80100400)]


@pytest.mark.parametrize('count,second',[(2,True),(3,False)])
def test_one_helper_or_short_chain_does_not_establish_mapping(count,second):
    image,taken,_=fixture(count,second)
    segments=[(0x3000,0x3300,0x80010000)]
    assert not transfers(image,taken)
    assert refine(image,segments,taken)==segments


def test_unknown_load_invalidates_a_constant_and_calls_clobber_argument_registers():
    image,taken,_=fixture()
    image=bytearray(image)
    # First ROM argument no longer comes from a known constant.
    image[0x1000:0x1004]=(0x8c100000).to_bytes(4,'big')
    assert not transfers(bytes(image),taken)


def test_overlapping_runtime_banks_are_not_selected():
    image,taken,ranges=fixture()
    # Known code already owns the destination; it is never moved or overridden.
    occupied=taken+[(0x2000,0x2100,ranges[0][2])]
    result=refine(image,[(0x3000,0x3100,0x80010000)],occupied)
    assert result==[(0x3000,0x3100,0x80010000)]


def test_constants_are_never_read_from_arbitrary_rom_data():
    image,_,_=fixture()
    assert not transfers(image,[(0x2000,0x2100,0x80002000)])


def test_branch_join_cannot_inherit_constants_from_another_path():
    image,taken,_=fixture()
    image=bytearray(image)
    image[0xff8:0xffc]=(0x10000003).to_bytes(4,'big')  # alternate entry at 0x1008
    taken=[(0xff8,taken[0][1],0x80000ff8)]
    assert not transfers(bytes(image),taken)


def test_conflicting_rom_destinations_are_left_for_review(monkeypatch):
    from decompnet.consoles.n64 import loader_mapping
    ranges=[(0x3000,0x3100,0x80100000),(0x3000,0x3100,0x80200000)]
    monkeypatch.setattr(loader_mapping,'transfers',lambda *_:ranges)
    segments=[(0x3000,0x3100,0x80010000)]
    assert refine(bytes(0x4000),segments)==segments
