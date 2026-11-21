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


def test_runtime_observed_range_needs_internal_call_corroboration():
    image,found=fixture()
    old=(0,0x4000,0x80040000)
    observed=(0,0x4000,0x80100000)
    mapped,changed=inferred_overlays.runtime_mappings(image,[old],[observed])
    assert mapped==[observed] and changed=={0}
    assert inferred_overlays.runtime_mappings(image,[old],[observed],[(0x5000,0x6000,0x80100000)])==([old],set())
    image[0x3000:0x3000+4*48]=bytes(4*48)
    assert inferred_overlays.runtime_mappings(image,[old],[observed])==([old],set())


def test_runtime_range_can_cover_only_part_of_inferred_code():
    image,found=fixture()
    old=(0,0x5000,0x80040000)
    observed=(0,0x4000,0x80100000)
    mapped,changed=inferred_overlays.runtime_mappings(image,[old],[observed])
    assert mapped==[(0,0x4000,0x80100000),(0x4000,0x5000,0x80044000)]
    assert changed=={0}


def test_rejected_partial_runtime_range_does_not_split_the_inferred_segment():
    image,found=fixture()
    image[0x3000:0x3000+4*48]=bytes(4*48)
    old=(0,0x5000,0x80040000)
    assert inferred_overlays.runtime_mappings(image,[old],[(0,0x4000,0x80100000)])==([old],set())


def test_scopes_cannot_overlap_the_global_range_even_in_its_gaps():
    found=[(0x2000,0x3000,0x80050000)]
    taken=[(0,0x1000,0x80000400),(0x1000,0x2000,0x80100000)]
    assert not inferred_overlays.scopes_fit(found,{0x2000},taken)
    assert inferred_overlays.scopes_fit([(0x2000,0x3000,0x80200000)],{0x2000},taken)


def test_scope_inferred_segments_below_banks_or_keep_original_for_known_conflict():
    found=[(0x1000,0x2000,0x80000400),(0x2000,0x3000,0x80050000)]
    taken=[(0,0x1000,0x80100000)]
    assert inferred_overlays.safe_scopes(found,{0x2000},taken)=={0x1000,0x2000}
    assert inferred_overlays.safe_scopes(found,{0x2000},[(0,0x100000,0x80000400)]) is None
