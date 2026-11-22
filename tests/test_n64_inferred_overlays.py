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


def test_shifted_overlay_needs_return_separated_entries_and_external_call():
    lo, hi = 0x1000, 0x2400
    correct = 0x80301000
    image = bytearray(hi)
    def jal(address):
        return 0x0c000000 | ((address >> 2) & 0x3ffffff)
    for i in range(16):
        target = correct + ((i + 1) % 16) * 32 + 8
        struct.pack_into('>8I', image, lo + i*32, 0x03e00008, 0, 0x3c038004, 0x24630010,
                         0x8c620000, jal(target), 0, 0)
    struct.pack_into('>I', image, 0, jal(correct + 8))
    found = [(lo, hi, correct - 12)]
    external = (0, 0x100, 0x80001000)
    result, scoped = inferred_overlays.recover(bytes(image), found, [external])
    assert result == [(lo, hi, correct)]
    assert lo in scoped
    image[:4] = bytes(4)
    assert inferred_overlays.recover(bytes(image), found, [external])[0] == found
    assert inferred_overlays.recover(bytes(image), [(lo, hi, correct)], [external])[0] == [(lo, hi, correct)]


def test_terminated_pointer_words_do_not_become_independent_code_scopes():
    image = bytearray(64)
    struct.pack_into('>3I', image, 16, 0x80001004, 0x80001008, 0)
    found = [(0, 16, 0x80001000), (16, 20, 0x80202000),
             (20, 24, 0x80202004), (24, 28, 0x80202008), (28, 64, 0x8020200c)]
    assert inferred_overlays.drop_pointer_fragments(bytes(image), found) == [found[0], found[4]]
    struct.pack_into('>I', image, 0, 0x08000000 | ((0x80202004 >> 2) & 0x3ffffff))
    assert inferred_overlays.drop_pointer_fragments(bytes(image), found) == found
