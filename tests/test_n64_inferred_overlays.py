"""Overlapping inferred mappings need many independent, unambiguous self-calls."""

import random
import struct

from decompnet.consoles.n64 import inferred_overlays


def fixture():
    image=bytearray(0x6000)
    offsets=sorted(random.Random(17).sample(range(0x10,0x2f0),48))
    base=0x80100000
    for i,slot in enumerate(offsets):
        at=slot*16
        struct.pack_into('>I',image,at,0x27bdffe0)
        target=base+at
        struct.pack_into('>I',image,0x3000+4*i,0x0c000000|((target>>2)&0x3ffffff))
    found=[(0,0x4000,0x80040000),(0x4000,0x5000,base+0x1000)]
    return image,found


def test_strong_unique_calls_recover_an_overlapping_bank_and_scope_it():
    image,found=fixture()
    result,scoped=inferred_overlays.recover(image,found)
    assert result==[(0,0x4000,0x80100000),found[1]]
    assert scoped=={0,0x4000}
    config={'segments':[dict(name='first',type='code',start=0),
                        dict(name='second',type='code',start=0x4000)]}
    inferred_overlays.scope(config,result,scoped)
    assert config['segments'][0]['exclusive_ram_id']==config['segments'][1]['exclusive_ram_id']


def test_never_override_a_known_mapping_or_infer_from_few_calls():
    image,found=fixture()
    assert inferred_overlays.recover(image,found,[(0x5000,0x6000,0x80100000)])[0]==found
    image[0x3000+4*12:0x3000+4*48]=bytes(4*36)
    assert inferred_overlays.recover(image,found)==(found,set())


def test_ambiguous_self_calls_leave_the_original_mapping():
    image,found=fixture()
    for i in range(48):
        word=struct.unpack_from('>I',image,0x3000+4*i)[0]
        struct.pack_into('>I',image,0x3200+4*i,word+0x4000)
    assert inferred_overlays.recover(image,found)==(found,set())
