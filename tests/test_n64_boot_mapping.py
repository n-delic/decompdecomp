"""A synthetic TLB startup must not swallow the compiled code following its return."""
import struct

import pytest
import yaml

from decompnet.consoles.n64.boot_mapping import recover
from decompnet.rom.split import segments_from_config
from tests.test_n64code import _code


def layout(tmp_path, *, tlb=True, count=16, mapping=0x801FF400, boot_padding=0):
    start=0x1080
    boot=bytes(boot_padding)+struct.pack('>4I',0x42000002 if tlb else 0,0,0x03E00008,0)
    body=_code(start+len(boot)+mapping,count)
    end=start+len(boot)+len(body)
    later=end+0x100
    rom=bytes(start)+boot+body+bytes(0x100)+_code(later+mapping,16)
    cfg={'options':{'platform':'n64'},'segments':[
        {'name':'entry','type':'code','start':0x1000,'vram':0x80000400,
         'subsegments':[[0x1000,'hasm']]},
        {'name':'main','type':'code','start':start,'vram':start+0x7FFFF400,
         'follows_vram':'entry','subsegments':[[start,'c','main']]},
        {'type':'bin','start':end},
        {'name':'later','type':'code','start':later,'vram':later+mapping,
         'subsegments':[[later,'c','later']]},[len(rom)]]}
    path=tmp_path/'game.yaml';path.write_text(yaml.safe_dump(cfg,sort_keys=False))
    return path,rom,start+len(boot),end


def test_tlb_boot_return_is_separated_from_a_strongly_supported_main_mapping(tmp_path):
    path,rom,start,end=layout(tmp_path)
    assert recover(path,rom,[],log=lambda _:None)
    segments=segments_from_config(path)
    boot=next(s for s in segments if s.name=='main')
    code=next(s for s in segments if s.name.startswith('code_'))
    assert boot.rom_end==start and boot.vram==0x80000480
    assert (code.rom_start,code.rom_end,code.vram)==(start,end,start+0x801FF400)
    saved=path.read_bytes()
    assert not recover(path,rom,[],log=lambda _:None)
    assert path.read_bytes()==saved


@pytest.mark.parametrize('case',['no_tlb','few_calls','correct_mapping','explicit_data','no_return','overlap','ambiguous','earlier_leaf','earlier_frame','explicit_bss','late_return'])
def test_uncertain_or_explicit_layouts_are_not_remapped(tmp_path,case):
    path,rom,start,end=layout(tmp_path,tlb=case!='no_tlb',count=4 if case=='few_calls' else 16,
                              mapping=0x7FFFF400 if case=='correct_mapping' else 0x801FF400,
                              boot_padding=0x1000 if case=='late_return' else 0)
    cfg=yaml.safe_load(path.read_text())
    if case=='explicit_data':cfg['segments'][1]['subsegments'].append([end-4,'data'])
    if case=='no_return':rom=rom[:start-8]+bytes(4)+rom[start-4:]
    if case=='earlier_frame':rom=rom[:start-12]+struct.pack('>I',0x27BDFFE8)+rom[start-8:]
    if case=='explicit_bss':cfg['segments'][1]['bss_size']=0x100

    if case=='overlap':cfg['segments'][3]['vram']=start+0x801FF400
    if case=='ambiguous':
        # The same functions called under a second established alias: no unique placement.
        alias=0x802FF400
        words=list(struct.unpack(f'>{len(rom)//4}I',rom))
        for offset in range(start,end,32):
            words[(offset+12)//4]=0x0C000000|(((offset+alias)>>2)&0x03FFFFFF)
        rom=struct.pack(f'>{len(words)}I',*words)
        cfg['segments'].insert(-1,{'name':'alias','type':'code','start':len(rom)-4,
                                  'vram':len(rom)-4+alias,'subsegments':[[len(rom)-4,'c','alias']]})
    if case=='earlier_leaf':
        # An earlier called entry would be lost if the first called frame alone set the cut.
        words=bytearray(rom)
        struct.pack_into('>I',words,start+12,0x0C000000|(((start-8+0x801FF400)>>2)&0x03FFFFFF))
        rom=bytes(words)
    path.write_text(yaml.safe_dump(cfg,sort_keys=False));before=path.read_bytes()
    assert not recover(path,rom,[],log=lambda _:None)
    assert path.read_bytes()==before


def test_segment_discovery_repairs_a_boot_mapping_even_without_additional_gaps(tmp_path,monkeypatch):
    from decompnet.consoles.n64 import code

    path,rom,start,end=layout(tmp_path)
    (tmp_path/'rom.z64').write_bytes(rom)
    monkeypatch.setattr(code,'unpack_code',lambda _:[])
    monkeypatch.setattr(code,'find_segments',lambda *a,**kw:[])
    assert code.add_segments(path,tmp_path,'rom.z64',log=lambda _:None)=='rom.z64'
    segments=segments_from_config(path)
    assert any(s.rom_start==start and s.rom_end==end and s.vram==start+0x801FF400 for s in segments)


def test_the_established_mapping_can_come_from_newly_discovered_code(tmp_path):
    path,rom,start,end=layout(tmp_path)
    cfg=yaml.safe_load(path.read_text());later=cfg['segments'][3]
    cfg['segments'][3]={'type':'bin','start':later['start']}
    path.write_text(yaml.safe_dump(cfg,sort_keys=False))
    assert recover(path,rom,[(later['start'],len(rom),later['vram'])],log=lambda _:None)
    assert any(s.rom_start==start and s.rom_end==end for s in segments_from_config(path))


@pytest.mark.parametrize('named_entry', [False, True])
def test_startup_break_belongs_to_the_boot_stub_before_a_called_helper(tmp_path, named_entry):
    from decompnet.consoles.n64.boot_mapping import extend_startup_trap

    path, rom, _, _ = layout(tmp_path)
    rom = bytearray(rom)
    call = lambda address: 0x0c000000 | (address>>2)&0x3ffffff
    struct.pack_into('>I',rom,0x1000,call(0x80000480 if named_entry else 0x80000488))
    struct.pack_into('>4I',rom,0x1078,call(0x800004c0),0,0x0000000d,0)
    assert extend_startup_trap(path,rom) != named_entry
    entry, main, *_ = segments_from_config(path)
    boundary = 0x1080 if named_entry else 0x1088
    assert entry.rom_end == main.rom_start == boundary
    assert main.vram == boundary+0x7ffff400


@pytest.mark.parametrize('referenced', [False, True])
def test_boot_data_terminator_precedes_a_called_aligned_frame(tmp_path, referenced):
    from decompnet.consoles.n64.boot_mapping import extend_startup_data

    path, rom, _, _ = layout(tmp_path)
    cfg = yaml.safe_load(path.read_text())
    entry, main = cfg['segments'][:2]
    entry['subsegments'].append([0x1020, 'data'])
    at, address = 0x107c, 0x8000047c
    main.update(start=at, vram=address, subsegments=[[at, 'c', 'main']])
    path.write_text(yaml.safe_dump(cfg))
    image = bytearray(rom)
    target = address if referenced else address+4
    call = 0x0c000000 | ((target >> 2) & 0x3ffffff)
    struct.pack_into('>8I', image, at, 0x41424300, 0x27bdffe8, 0xafbf0014,
                     call, 0, 0x8fbf0014, 0x03e00008, 0x27bd0018)
    assert extend_startup_data(path, image) != referenced
    assert segments_from_config(path)[0].rom_end == at+(0 if referenced else 4)


@pytest.mark.parametrize('trap', [0, 0x0000000d])
def test_version_stamp_is_read_only_data_when_it_follows_the_boot_trap(tmp_path, trap):
    from decompnet.consoles.n64.boot_mapping import mark_boot_version
    from decompnet.rom.split import code_ranges

    path, rom, _, _ = layout(tmp_path)
    cfg = yaml.safe_load(path.read_text())
    _, main, data, *_ = cfg['segments']
    at = 0x1014
    main.update(start=at, vram=0x80000414, subsegments=[[at,'c','main']])
    data['start'] = at+12
    path.write_text(yaml.safe_dump(cfg))
    image = bytearray(rom)
    struct.pack_into('>3I', image, at-12, 0x0c000400, 0, trap)
    stamp = b'Version date 1/7/2000 02:03:04\0'
    image[at:at+len(stamp)] = stamp
    assert mark_boot_version(path, image) == bool(trap)
    assert all(lo != at for lo,_,_ in code_ranges(path)) == bool(trap)


@pytest.mark.parametrize('referenced', [False, True])
def test_startup_trap_and_printable_descriptor_precede_called_frame(tmp_path, referenced):
    from decompnet.consoles.n64.boot_mapping import extend_startup_trap

    path, rom, _, _ = layout(tmp_path)
    rom = bytearray(rom)
    target = 0x80000480 if referenced else 0x80000488
    call = lambda address: 0x0c000000 | (address >> 2) & 0x3ffffff
    struct.pack_into('>4I', rom, 0x1078, call(target), 0, 0x0001008d, 0x20696e74)
    struct.pack_into('>I', rom, 0x1088, 0x27bdffe8)
    assert extend_startup_trap(path, rom, log=lambda _: None) != referenced
    entry, main, *_ = segments_from_config(path)
    assert entry.rom_end == main.rom_start == (0x1080 if referenced else 0x1088)
