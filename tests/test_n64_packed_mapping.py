"""A packed block's link address needs many independent closed call targets."""
import random
import struct

import pytest

from decompnet.consoles.n64 import packed_mapping


def fixture():
    image=bytearray(0x6000);lo,hi=0x1000,0x3000;base=0x808c0000
    slots=sorted(random.Random(0).sample(range(1,120),12))
    for i,slot in enumerate(slots):
        at=lo+slot*0x40;target=base+slots[(i+1)%len(slots)]*0x40
        words=(0x27bdffe8,0xafbf0014,0x0c000000|((target>>2)&0x3ffffff),0,0x8fbf0014,0x03e00008,0x27bd0018)
        struct.pack_into('>7I',image,at,*words)
    return image,(lo,hi),base,slots


def test_packed_mapping_recovers_closed_entries_at_their_link_addresses():
    image,block,base,slots=fixture();lo,hi=block;cut=lo+slots[6]*0x40
    found=[(lo,cut,0x80200000),(cut,hi,0x80200000+cut-lo)]
    result,changed=packed_mapping.recover(image,found,[block])
    assert result==[(lo,cut,base),(cut,hi,base+cut-lo)] and changed=={lo,cut}
    assert packed_mapping.recover(image,found,[block],[(0,0x2000,base)])==(found,set())


def test_fragment_without_its_own_closed_target_keeps_its_mapping():
    image,(lo,hi),base,slots=fixture();cut=lo+(slots[-1]+1)*0x40
    found=[(lo,cut,0x80200000),(cut,hi,0x80200000+cut-lo)]
    result,changed=packed_mapping.recover(image,found,[(lo,hi)])
    assert result==[(lo,cut,base),found[1]] and changed=={lo}


def test_packed_mapping_rejects_insufficient_or_ambiguous_evidence():
    image,block,base,slots=fixture();lo,hi=block;found=[(lo,hi,0x80200000)]
    for slot in slots[4:]:struct.pack_into('>I',image,lo+slot*0x40+8,0)
    assert packed_mapping.recover(image,found,[block])==(found,set())
    image,block,base,slots=fixture()
    # A second independently linked code family with equally strong calls is not one proven mapping.
    for i,slot in enumerate(slots):
        at=lo+slot*0x40+0x2000;target=base+0x100000+slots[(i+1)%len(slots)]*0x40
        words=(0x27bdffe8,0xafbf0014,0x0c000000|((target>>2)&0x3ffffff),0,0x8fbf0014,0x03e00008,0x27bd0018)
        struct.pack_into('>7I',image,at,*words)
    assert packed_mapping.recover(image,found,[(lo,0x6000)])==(found,set())


def test_packed_mapping_checks_cancellation():
    image,block,_,_=fixture()
    def stop():raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError,match='Stopped'):
        packed_mapping.recover(image,[(block[0],block[1],0x80200000)],[block],check=stop)
