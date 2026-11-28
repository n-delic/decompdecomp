"""Overlapping inferred mappings need many independent, unambiguous self-calls."""

import random
import struct

import pytest

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


def test_close_scores_need_unique_closed_callees():
    image = bytearray(0x3000)
    mapping, other = 0x80010000, 0x80011000
    body = (0x27bdffe8, 0xafbf0014, 0x24020007, 0x8fbf0014, 0x03e00008, 0x27bd0018)
    targets = set()
    for index in range(12):
        at = 0x1200+index*0x40
        struct.pack_into('>6I', image, at, *body)
        struct.pack_into('>I', image, at-0x1000, body[0])
        targets.add(mapping+at)
    scores = {mapping: 40, other: 8}
    assert inferred_overlays._unique_closed_mapping(image, 0, len(image), targets, scores, mapping, lambda: None)
    for index in range(12):
        struct.pack_into('>6I', image, 0x200+index*0x40, *body)
    assert not inferred_overlays._unique_closed_mapping(image, 0, len(image), targets, scores, mapping, lambda: None)


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


def test_runtime_bank_continuation_needs_shared_observation_and_two_closed_callees():
    image,_=fixture();base=0x80100000
    first=(0,0x4000,0x80040000);second=(0x4000,0x5000,0x80090000)
    observed=(0,0x5000,base)
    body=(0x27bdffe8,0xafbf0014,0x24020007,0x8fbf0014,0x03e00008,0x27bd0018)
    for at in (0x4000,0x4040):struct.pack_into('>6I',image,at,*body)
    def jal(address):return 0x0c000000|((address>>2)&0x3ffffff)
    struct.pack_into('>2I',image,0x3200,jal(base+0x4000),jal(base+0x4040))
    mapped,changed=inferred_overlays.runtime_mappings(image,[first,second],[observed])
    assert mapped==[(0,0x4000,base),(0x4000,0x5000,base+0x4000)] and changed=={0,0x4000}
    # Two independently observed pieces do not establish that they coexisted.
    assert inferred_overlays.runtime_mappings(image,[first,second],[(0,0x4000,base),(0x4000,0x5000,base+0x4000)])[0][1]==second
    saved=image[0x3000:0x3000+4*48];image[0x3000:0x3000+4*48]=bytes(4*48)
    assert inferred_overlays.runtime_mappings(image,[first,second],[observed])[0]==[first,second]
    image[0x3000:0x3000+4*48]=saved
    struct.pack_into('>I',image,0x3204,0)
    assert inferred_overlays.runtime_mappings(image,[first,second],[observed])[0][1]==second
    struct.pack_into('>I',image,0x3204,jal(base+0x4040))
    struct.pack_into('>I',image,0x4040,0)
    assert inferred_overlays.runtime_mappings(image,[first,second],[observed])[0][1]==second
    struct.pack_into('>6I',image,0x4040,*body)
    struct.pack_into('>I',image,0x3208,jal(second[2]))
    assert inferred_overlays.runtime_mappings(image,[first,second],[observed])[0][1]==second


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


def test_nearby_mapping_requires_closed_entries_and_internal_and_external_calls():
    image = bytearray(0x5000)
    lo, hi, correct = 0x1000,0x4000,0x80301000
    def jal(target):
        return 0x0c000000 | ((target>>2)&0x3ffffff)
    for i in range(12):
        at = lo+i*64
        # The complete framed body is not closed when entered eight bytes late.
        struct.pack_into('>8I',image,at,0x27bdfff0,0xafbf000c,0x24020001,0,
                         0x8fbf000c,0,0x03e00008,0x27bd0010)
        struct.pack_into('>I',image,0x3000+4*i,jal(correct+i*64))
        if i < 4:
            struct.pack_into('>I',image,4*i,jal(correct+i*64))
    found = [(lo,hi,correct-8)]
    taken = [(0,0x100,0x80001000)]
    result, scoped = inferred_overlays.recover(bytes(image),found,taken)
    assert result == [(lo,hi,correct)] and lo in scoped
    image[:16] = bytes(16)
    assert inferred_overlays.recover(bytes(image),found,taken)[0] == found


def test_concatenated_banks_require_distinct_self_calls_and_data_gap():
    image = bytearray(0xb000)
    lo, boundary, hi = 0x1000, 0x5000, 0xb000
    old, alternate = 0x80100000, 0x80104000
    # Closed functions in each bank, and calls to their own placements.
    first = [lo + i*0x100 for i in range(8)]
    second = [boundary + i*0x100 for i in range(8)]
    for at in first + second:
        struct.pack_into('>6I', image, at, 0x27bdffe0, 0xafbf001c, 0x8fbf001c,
                         0x27bd0020, 0x03e00008, 0)
    struct.pack_into('>2I', image, 0x3ff8, 0x03e00008, 0)
    for i, at in enumerate(first + second):
        target = at + (alternate-lo if at < boundary else old-lo)
        call = 0x3000 + 4*i if i < 8 else 0x9000 + 4*(i-8)
        struct.pack_into('>I', image, call, 0x0c000000 | ((target >> 2) & 0x3ffffff))
    found = [(lo, hi, old)]
    assert inferred_overlays.split_concatenated(image, found) == (
        [(lo, 0x4000, alternate), (boundary, hi, alternate)], {lo, boundary})
    # Without enough independent entries the original map remains untouched.
    image[0x3000:0x3020] = bytes(0x20)
    assert inferred_overlays.split_concatenated(image, found) == (found, set())


def test_truncated_terminal_frame_extends_only_to_a_closed_next_boundary():
    image = bytearray(0x400)
    struct.pack_into('>2I', image, 0x1ec, 0x03e00008, 0)
    struct.pack_into('>7I', image, 0x1f4, 0x27bdffe0, 0xafbf001c, 0, 0x8fbf001c,
                     0x27bd0020, 0x03e00008, 0)
    found = [(0x100, 0x200, 0x80100000), (0x210, 0x300, 0x80110000)]
    assert inferred_overlays.extend_adjacent_frame(image, found) == [
        (0x100, 0x210, 0x80100000), found[1]]
    image[0x208:0x20c] = bytes(4)
    assert inferred_overlays.extend_adjacent_frame(image, found) == found


def test_same_address_banks_get_distinct_segment_names(tmp_path, monkeypatch):
    import yaml

    from decompnet.consoles.n64.code import insert_segments
    from decompnet.rom import split

    config = tmp_path / 'game.yaml'
    config.write_text('segments: []\n')
    monkeypatch.setattr(split, 'segments_from_config', lambda _: [])
    cfg = {'segments': [[0x4000]]}
    banks = [(0x1000, 0x1800, 0x80100000), (0x2000, 0x3000, 0x80100000)]
    insert_segments(config, cfg, banks, 0x4000)
    rows = [row for row in yaml.safe_load(config.read_text())['segments']
            if isinstance(row, dict) and row.get('type') == 'code']
    assert len(rows) == 2 and len({row['name'] for row in rows}) == 2
    assert all(row['subsegments'][0][2] == row['name'] for row in rows)


def test_tiny_observed_fragments_join_the_corroborated_runtime_bank():
    image, _ = fixture()
    old = (0, 0x5000, 0x80040000)
    observed = [(0, 4, 0x80100000), (4, 8, 0x80100004), (8, 0x4000, 0x80100008)]
    mapped, changed = inferred_overlays.runtime_mappings(image, [old], observed)
    assert mapped == [(0, 0x4000, 0x80100000), (0x4000, 0x5000, 0x80044000)]
    assert changed == {0}
    # Conflicting observations cannot supply a mapping for the small fragment.
    observed.append((0, 4, 0x80200000))
    mapped, _ = inferred_overlays.runtime_mappings(image, [old], observed)
    assert mapped[0] == (0, 4, 0x80040000)


@pytest.mark.parametrize('second_start', [0x1860, 0x1a00])
def test_separate_called_banks_leave_intervening_data_unmapped(second_start):
    image = bytearray(0x4000)
    rng = random.Random(491)
    def group(first, last):
        selected = [first, last]
        while len(selected) < 17:
            at = rng.randrange(first+32, last-32, 16)
            if all(abs(at-other) >= 48 for other in selected):
                selected.append(at)
        return sorted(selected)
    groups = [group(0, 0x1800), group(second_start, 0x3f00)]
    mappings = (0x80010000, 0x80022000)
    for group, mapping in zip(groups, mappings):
        for i, at in enumerate(group):
            target = group[(i+1) % len(group)] + mapping
            jump = 0x08000000 | (((at+24+mapping) >> 2) & 0x3ffffff)
            words = (0x27bdffe8, 0xafbf0014, 0x0c000000 | ((target >> 2) & 0x3ffffff),
                     0x00000000, jump, 0, 0x8fbf0014, 0x03e00008, 0x27bd0018)
            struct.pack_into('>9I', image, at, *words)
    end = groups[0][-1] + 36
    start = groups[1][0]
    image[end:start] = b'\x01\x02\x03\x04' * ((start-end)//4)
    found = [(0, len(image), 0x80400000)]
    result, changed = inferred_overlays.split_concatenated(image, found)
    assert result == [(0, end, mappings[0]), (start, len(image), start+mappings[1])]
    assert changed == {0, start}
    # A call from an already mapped bank can also make the gap executable.
    target = mappings[0] + end
    image.extend(struct.pack('>I', 0x0c000000 | ((target >> 2) & 0x3ffffff)))
    assert inferred_overlays.split_concatenated(image, found, taken=[(0x4000, 0x4004, 0x80000400)]) == (found, set())
    # A call into its own independently mapped bank does not establish an
    # entry in a different bank that shares the runtime address.
    result, _ = inferred_overlays.split_concatenated(image, found, taken=[(0x4000, 0x4004, target)])
    assert result == [(0, end, mappings[0]), (start, 0x4000, start+mappings[1])]
    # A frame hidden in the gap makes its ownership uncertain.
    struct.pack_into('>I', image, end+4, 0x27bdffe8)
    assert inferred_overlays.split_concatenated(image, found) == (found, set())
    # A leaf return is also evidence that the gap may contain code.
    struct.pack_into('>2I', image, end+4, 0x01020304, 0x03e00008)
    assert inferred_overlays.split_concatenated(image, found) == (found, set())


def test_resident_bss_can_share_a_single_overlapping_overlay_scope():
    config = {'segments': [
        {'type': 'code', 'name': 'main', 'start': 0x1000, 'vram': 0x80000000, 'bss_size': 0x3000},
        {'type': 'code', 'name': 'overlay', 'start': 0x2000, 'vram': 0x80003000,
         'exclusive_ram_id': 'slot'},
        [0x3000],
    ]}
    assert inferred_overlays.scope_resident_bss(config)
    assert config['segments'][0]['exclusive_ram_id'] == 'slot'
    broken = {'segments': [dict(config['segments'][0]), dict(config['segments'][1]), [0x3000]]}
    broken['segments'][0].pop('exclusive_ram_id')
    broken['segments'][1]['vram'] = 0x80000800
    assert not inferred_overlays.scope_resident_bss(broken)
    assert 'exclusive_ram_id' not in broken['segments'][0]
    ambiguous = {'segments': [dict(config['segments'][0]), dict(config['segments'][1]),
                              {'type': 'code', 'name': 'other', 'start': 0x2800, 'vram': 0x80002800,
                               'exclusive_ram_id': 'other-slot'}, [0x3000]]}
    ambiguous['segments'][0].pop('exclusive_ram_id')
    assert not inferred_overlays.scope_resident_bss(ambiguous)
    assert 'exclusive_ram_id' not in ambiguous['segments'][0]


def test_extend_tail_wrapper_requires_separated_entry_and_mapped_tail():
    image = bytearray(0x100)
    words = [0x03e00008, 0, 0x2483ff20, 0x000310c0, 0x00431023,
             0x08000420, 0x00432021]
    for i, word in enumerate(words):
        struct.pack_into('>I', image, 0x20 + 4*i, word)
    bank = [(0x10, 0x30, 0x80001000)]
    target = [(0x80, 0x90, 0x80001080)]
    assert inferred_overlays.extend_tail_wrapper(image, bank, target) == [(0x10, 0x3c, 0x80001000)]
    assert inferred_overlays.extend_tail_wrapper(image, bank, []) == bank
    struct.pack_into('>I', image, 0x30, 0x10000001)
    assert inferred_overlays.extend_tail_wrapper(image, bank, target) == bank
