import struct

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
    monkeypatch.setattr(code,'find_segments',lambda *_:[(32,64,0x80000420),(64,128,0x80000440)])
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
