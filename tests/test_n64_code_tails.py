import struct

import pytest

from decompnet.consoles.n64.code_tails import complete


def image(body):
    words=[0x03e00008,0,0x27bdffe8]+body
    return struct.pack('>'+str(len(words))+'I',*words)


def test_alignment_prologue_extends_through_a_long_quiet_body():
    rom=image([0]*1000+[0x03e00008,0x27bd0018])
    assert complete(rom,[(0,12,0x80001000)])==[(0,len(rom),0x80001000)]


def test_requires_a_balanced_return_and_valid_instructions():
    for body in ([0x03e00008,0x27bd0010],[0x7000003f,0x03e00008,0x27bd0018],[0]*20):
        rom=image(body)
        assert complete(rom,[(0,12,0x80001000)])==[(0,12,0x80001000)]


def test_does_not_cross_another_segment_or_forward_branch_target():
    rom=image([0]*20+[0x03e00008,0x27bd0018])
    assert complete(rom,[(0,12,0x80001000)],[(40,len(rom),0x90001000)])==[(0,12,0x80001000)]
    rom=image([0x10000008,0,0x03e00008,0x27bd0018]+[0]*10)
    assert complete(rom,[(0,12,0x80001000)])==[(0,12,0x80001000)]


def test_normal_completed_segment_is_unchanged():
    rom=image([0]*20+[0x03e00008,0x27bd0018])
    assert complete(rom,[(0,len(rom),0x80001000)])==[(0,len(rom),0x80001000)]


def test_called_leaf_after_return_can_extend_through_closed_branches():
    words = [0x03e00008, 0, 0x3c02800c, 0x8c420000, 0x10400002, 0, 0x24420001, 0x03e00008, 0]
    rom = struct.pack('>9I', *words)
    segment = (0, 16, 0x80001000)
    assert complete(rom, [segment], called={0x80001008}) == [(0, len(rom), 0x80001000)]
    assert complete(rom, [segment]) == [segment]
    assert complete(rom, [segment], [(24, len(rom), 0x90001000)], called={0x80001008}) == [segment]
    assert complete(rom, [segment], called={0x80001008, 0x80001018}) == [segment]
    damaged = bytearray(rom)
    struct.pack_into('>I', damaged, 16, 0x10407fff)
    assert complete(damaged, [segment], called={0x80001008}) == [segment]


def test_loads_before_frame_in_alignment_tail_are_completed():
    words=[0x03e00008,0,0x3c02800c,0x8c427f84,0x27bdffe8]+[0]*1000+[0x03e00008,0x27bd0018]
    rom=struct.pack('>'+str(len(words))+'I',*words)
    assert complete(rom,[(0,16,0x80001000)])==[(0,len(rom),0x80001000)]


def test_touching_discovered_segments_do_not_add_zero_length_bins(tmp_path,monkeypatch):
    import yaml

    from decompnet.consoles.n64 import code
    cfg={'name':'synthetic','options':{},'segments':[
        {'name':'main','type':'code','start':0,'vram':0x80000400,'subsegments':[[0,'asm']]},
        {'type':'bin','start':32},[256]]}
    path=tmp_path/'synthetic.yaml';path.write_text(yaml.safe_dump(cfg))
    (tmp_path/'rom.z64').write_bytes(bytes(256))
    monkeypatch.setattr(code,'unpack_code',lambda _:[])
    monkeypatch.setattr(code,'find_segments',lambda *_,**__:[(32,64,0x80000420),(64,128,0x80000440)])
    code.add_segments(path,tmp_path,'rom.z64',log=lambda _:None)
    segments=yaml.safe_load(path.read_text())['segments']
    starts=[s[0] if isinstance(s,list) else s['start'] for s in segments]
    assert len(starts)==len(set(starts))


def test_recovery_respects_same_ram_segment_and_scan_bound():
    rom=image([0]*20+[0x03e00008,0x27bd0018])
    assert complete(rom,[(0,12,0x80001000)],[(1000,1100,0x80001020)])==[(0,12,0x80001000)]
    from decompnet.consoles.n64.code_tails import MAX_TAIL
    rom=image([0]*(MAX_TAIL//4)+[0x03e00008,0x27bd0018])
    assert complete(rom,[(0,12,0x80001000)])==[(0,12,0x80001000)]


def test_a_segment_truncated_to_one_or_two_prologue_instructions_is_completed():
    rom=struct.pack('>7I',0x27bdffe8,0xafbf0014,0,0,0x8fbf0014,0x03e00008,0x27bd0018)
    for end in (4,8):
        assert complete(rom,[(0,end,0x80001000)])==[(0,len(rom),0x80001000)]


def test_a_standalone_partial_prologue_never_crosses_another_parent_or_unbalanced_return():
    rom=struct.pack('>7I',0x27bdffe8,0xafbf0014,0,0,0x8fbf0014,0x03e00008,0x27bd0010)
    assert complete(rom,[(0,4,0x80001000)])==[(0,4,0x80001000)]
    rom=rom[:-4]+struct.pack('>I',0x27bd0018)
    assert complete(rom,[(0,4,0x80001000)],[(16,28,0x90001000)])==[(0,4,0x80001000)]


def test_long_scheduled_prologue_requires_independent_call():
    words = [0x03e00008, 0] + [0x3c028001] * 8 + [0x27bdffe8, 0, 0x03e00008, 0x27bd0018]
    rom = struct.pack('>' + str(len(words)) + 'I', *words)
    segment = [(0, 12, 0x80001000)]
    assert complete(rom, segment) == segment
    assert complete(rom, segment, called={0x80001008}) == [(0, len(rom), 0x80001000)]


def test_called_tail_accepts_a_straight_line_stack_pop_before_return():
    words = [0x03e00008, 0] + [0x3c028001] * 8 + [0x27bdffe8, 0, 0x27bd0018, 0x03e00008, 0]
    rom = struct.pack('>' + str(len(words)) + 'I', *words)
    assert complete(rom, [(0, 12, 0x80001000)], called={0x80001008}) == [(0, len(rom), 0x80001000)]


def test_complete_tail_can_overlap_only_an_inferred_bank_when_scoped():
    body = image([0]*20+[0x03e00008, 0x27bd0018])
    rom = body + bytes(64)
    first = (0, 12, 0x80001000)
    other = (len(body), len(rom), 0x80001020)
    assert complete(rom, [first, other])[0] == first
    assert complete(rom, [first, other], allow_inferred_overlap=True)[0] == (0, len(body), 0x80001000)
    assert complete(rom, [first], [other], allow_inferred_overlap=True)[0] == first


def test_detected_code_removes_old_bin_marker_inside_its_extent(tmp_path):
    import yaml

    from decompnet.consoles.n64.code import insert_segments

    cfg={'segments':[{'name':'main','type':'code','start':0,'vram':0x80000400,
                      'subsegments':[[0,'c','main']]},
                     {'type':'bin','start':48},[128]]}
    path=tmp_path/'synthetic.yaml';path.write_text(yaml.safe_dump(cfg))
    insert_segments(path,cfg,[(32,96,0x80001000)],128)
    segments=yaml.safe_load(path.read_text())['segments']
    starts=[s[0] if isinstance(s,list) else s['start'] for s in segments]
    assert starts == [0,32,96,128]


@pytest.mark.parametrize('case', ['boundary', 'later_child', 'different_mapping', 'duplicate_child'])
def test_recovered_code_can_start_at_a_guessed_data_tail(case, tmp_path):
    import yaml

    from decompnet.consoles.n64.code import insert_segments
    from decompnet.rom import RomError

    base = 0x80000400
    children = [[0, 'c', 'main'], [32, 'data'], {'type': 'bss', 'vram': base+128}]
    if case == 'later_child': children.append([96, 'c', 'later'])
    if case == 'duplicate_child': children.append([32, 'c', 'other'])
    cfg = {'segments': [{'name': 'main', 'type': 'code', 'start': 0, 'vram': base, 'bss_size': 64,
                         'subsegments': children}, {'type': 'bin', 'start': 128}, [256]]}
    path = tmp_path/'synthetic.yaml'
    path.write_text(yaml.safe_dump(cfg))
    body = (32, 80, base+32+(4 if case == 'different_mapping' else 0))
    if case != 'boundary':
        with pytest.raises(RomError, match='crosses an existing bank'):
            insert_segments(path, cfg, [body], 256)
        return
    insert_segments(path, cfg, [body], 256)
    segments = yaml.safe_load(path.read_text())['segments']
    assert segments[0]['subsegments'][0] == [0, 'c', 'main']
    assert segments[0]['subsegments'][1][:2] == [32, 'c']
    assert segments[0]['subsegments'][2][:2] == [80, 'data']
    assert segments[0]['subsegments'][3] == {'type': 'bss', 'vram': base+128}
    assert segments[0]['bss_size'] == 64
    assert segments[1]['start'] == 128


def test_recovered_tail_ranges_keep_gaps_and_one_parent(tmp_path):
    import yaml

    from decompnet.consoles.n64.code import insert_segments

    base = 0x80000400
    cfg = {'segments': [{'name': 'main', 'type': 'code', 'start': 0, 'vram': base,
                         'subsegments': [[0, 'c', 'main'], [32, 'data']]},
                        {'type': 'bin', 'start': 160}, [256]]}
    path = tmp_path/'synthetic.yaml'
    path.write_text(yaml.safe_dump(cfg))
    insert_segments(path, cfg, [(32, 64, base+32), (64, 96, base+64), (112, 128, base+112)], 256)
    segments = yaml.safe_load(path.read_text())['segments']
    assert [(s[0], s[1]) for s in segments[0]['subsegments']] == [
        (0, 'c'), (32, 'c'), (64, 'c'), (96, 'data'), (112, 'c'), (128, 'data')]
    assert segments[1]['start'] == 160


def test_shared_epilogue_may_branch_to_stack_restore_but_not_skip_it():
    rom = image([0x10000001, 0, 0x27bd0018, 0x03e00008, 0])
    assert complete(rom, [(0, 12, 0x80001000)]) == [(0, len(rom), 0x80001000)]
    skips_restore = image([0x10000002, 0, 0x27bd0018, 0x03e00008, 0])
    assert complete(skips_restore, [(0, 12, 0x80001000)]) == [(0, 12, 0x80001000)]
