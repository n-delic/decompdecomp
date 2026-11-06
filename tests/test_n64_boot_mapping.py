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
    monkeypatch.setattr(code,'find_segments',lambda *a:[])
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
