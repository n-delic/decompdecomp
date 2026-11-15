"""Dropped .word text is recovered through direct calls, not arbitrary instruction patterns."""
import struct

import pytest
import yaml

from decompnet.arch.mips_code_recovery import called_extent
from decompnet.consoles import get
from decompnet.rom import mixed_code

BASE=0x80001000
BODY=[0x27BDFFE8,0xAFBF0014,0x24020007,0x8FBF0014,0x03E00008,0x27BD0018]


def packed(words,big=True):
    return struct.pack(('>' if big else '<')+f'{len(words)}I',*words)


@pytest.mark.parametrize('big',[True,False])
def test_a_closed_frame_requires_valid_bytes_and_balanced_returns(big):
    data=packed(BODY,big)
    assert called_extent(data,0,len(data),BASE,big_endian=big)==len(data)
    for words in (BODY[:-1],BODY[:-1]+[0x27BD0010],BODY[:2]+[0x7000003F]+BODY[3:],
                  BODY[:2]+[0x27BDFFE8]+BODY[3:],BODY[:2]+[0x40906000]+BODY[3:],
                  BODY[:2]+[0x10001000]+BODY[3:],BODY[:2]+[0x1000FFFD,0]+BODY[4:]):
        data=packed(words,big)
        assert called_extent(data,0,len(data),BASE,big_endian=big) is None


def asm(path,words,start,*,raw=False,big=True):
    path.parent.mkdir(parents=True,exist_ok=True)
    title='dlabel dropped' if raw else 'glabel caller'
    import rabbitizer

    # Only mnemonic spelling matters here: callers must be decoded CPU instructions.
    lines=[title]
    for i,w in enumerate(words):
        code=w.to_bytes(4,'big' if big else 'little')
        mnemonic='.word' if raw else rabbitizer.Instruction(w).getOpcodeName()
        lines.append(f'/* {start+i*4:X} {BASE+start+i*4:08X} {code.hex().upper()} */ {mnemonic} 0')
    path.write_text('\n'.join(lines)+'\n')


def project(tmp_path,*,call=True,explicit=False,overlap=False,big=True,console_id=None):
    console=get(console_id or ('n64' if big else 'ps'))
    caller=[0x27BDFFE8,0xAFBF0014,0x0C000000|((BASE+0x40)>>2)&0x3FFFFFF,0,
            0x8FBF0014,0x27BD0018,0x03E00008,0]
    if not call:caller[2]=0
    words=caller+[0x7000003F]*8+BODY+[0]*10
    rom=packed(words,big);(tmp_path/'rom.bin').write_bytes(rom)
    cfg={'options':{},'segments':[{'name':'unit','type':'code','start':0,'vram':BASE,
         'subsegments':[[0,'c','unit']]+([[32,'data','data']] if explicit else [])},[len(rom)]]}
    if overlap:cfg['segments'].insert(-1,{'name':'overlay','type':'code','start':len(rom)-4,'vram':BASE+0x40,
                                        'subsegments':[[len(rom)-4,'c','overlay']]})
    path=tmp_path/'game.yaml';path.write_text(yaml.safe_dump(cfg,sort_keys=False))
    asm(tmp_path/'asm/nonmatchings/unit/caller.s',caller,0,big=big)
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s',words[8:],32,raw=True,big=big)
    return path,console


@pytest.mark.parametrize('console_id',['n64','ps','ps2','psp'])
def test_only_called_unowned_text_gets_new_source_boundaries(tmp_path,console_id):
    console=get(console_id)
    path,console=project(tmp_path,big=console.big_endian,console_id=console_id)
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    cfg=yaml.safe_load(path.read_text());subs=cfg['segments'][0]['subsegments']
    assert [s[0] for s in subs]==[0,0x20,0x40,0x58]
    assert subs[0]==[0,'c','unit']
    assert len(json_notes(tmp_path))==1
    # The unchanged old raw file cannot keep adding boundaries.
    assert not mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)


def json_notes(tmp_path):
    import json

    return json.loads((tmp_path/'analysis-coverage.json').read_text())


@pytest.mark.parametrize('case',['no_call','data','overlap','invalid','bad_caller'])
def test_weak_or_ambiguous_evidence_cannot_reclassify_data(tmp_path,case):
    path,console=project(tmp_path,call=case!='no_call',explicit=case=='data',overlap=case=='overlap')
    if case=='invalid':
        data=bytearray((tmp_path/'rom.bin').read_bytes());data[0x54:0x58]=bytes(4);(tmp_path/'rom.bin').write_bytes(data)
    if case=='bad_caller':
        asm(tmp_path/'asm/nonmatchings/unit/caller.s',[0x40806000,0x0C000000|((BASE+0x40)>>2)&0x3FFFFFF,0,0x03E00008,0],0)
    before=path.read_bytes()
    assert not mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    assert path.read_bytes()==before


@pytest.mark.parametrize('corrupt',[False,True])
def test_the_real_split_pipeline_recovers_code_after_invalid_words(tmp_path,monkeypatch,corrupt):
    from decompnet.rom import split

    image=bytearray(0x2000);image[:4]=bytes.fromhex('80371240')
    target=BASE+0x10
    caller=[0x27BDFFE8,0xAFBF0014,0x0C000000|((target>>2)&0x3FFFFFF),0,
            0x8FBF0014,0x03E00008,0x27BD0018,0]
    image[0x1080:0x10A0]=packed(caller)
    image[0x1000:0x1040]=packed([0x03E00008,0x24020003,0x7000003F,0x7000003F]+BODY+[0x7000003F]*6)
    (tmp_path/'rom.z64').write_bytes(image)
    cfg={'name':'synthetic mixed text','options':{'basename':'mixed','target_path':'rom.z64',
         'base_path':'.','platform':'n64','compiler':'KMC','o_as_suffix':True},'segments':[
         [0,'bin','header'],{'name':'mixed','type':'code','start':0x1000,'vram':BASE,
          'subsegments':[[0x1000,'c','mixed']]},[0x1040,'bin','padding'],
         {'name':'caller','type':'code','start':0x1080,'vram':BASE+0x80,
          'subsegments':[[0x1080,'c','caller']]},[0x10A0,'bin','tail'],[len(image)]]}
    real=split._splat;passes=[]
    def splat(args,root,log,**kwargs):
        if args[0]=='create_config':
            (root/'mixed.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
            return ''
        output=real(args,root,log,**kwargs)
        if corrupt and len(passes)==1:
            for path in (root/'asm/nonmatchings').glob('**/*.s'):
                fn=split.parse_function(path,path.parent.name)
                if fn and fn.vram==BASE+0x80:path.unlink()
        passes.append([p.read_text() for p in (root/'asm/nonmatchings').glob('**/*.s')])
        return output
    monkeypatch.setattr(split,'_splat',splat)
    result=split.split_rom(tmp_path,console=get('n64'),log=lambda _:None)
    if corrupt:
        assert len(passes)==3
        assert not any(f.vram==target for f in result.functions)
        assert any('original split was retained' in note for note in result.coverage_notes)
        assert any(f.vram==BASE+0x80 for f in result.functions)
        return
    assert len(passes)==2
    assert any('dlabel' in text and '7000003F' in text for text in passes[0])
    fn=next(f for f in result.functions if f.vram==target)
    assert [line.word for line in fn.lines]==BODY
    known=next(f for f in result.functions if f.vram==BASE)
    assert [line.word for line in known.lines]==[0x03E00008,0x24020003]
    assert any('Recovered 1 directly called' in note for note in result.coverage_notes)
    assert (tmp_path/'rom.z64').read_bytes()==image


def test_a_later_pass_resolves_a_new_source_unit_back_to_its_code_parent(tmp_path):
    import shutil

    path,console=project(tmp_path)
    data=bytearray((tmp_path/'rom.bin').read_bytes());data.extend(bytes(0xC0-len(data)))
    data[0x80:0x98]=packed(BODY);(tmp_path/'rom.bin').write_bytes(data)
    cfg=yaml.safe_load(path.read_text());cfg['segments'][-1]=[len(data)]
    path.write_text(yaml.safe_dump(cfg,sort_keys=False))
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s',list(struct.unpack('>40I',data[32:])),32,raw=True)
    visited=set()
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None,visited=visited)
    shutil.rmtree(tmp_path/'asm')
    caller=[0x27BDFFE8,0xAFBF0014,0x0C000000|(((BASE+0x80)>>2)&0x3FFFFFF),0,0x8FBF0014,0x27BD0018,0x03E00008,0]
    asm(tmp_path/'asm/nonmatchings/unit/caller.s',caller,0)
    asm(tmp_path/'asm/nonmatchings/unit_000058/dropped.s',list(struct.unpack('>26I',data[0x58:])),0x58,raw=True)
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None,visited=visited)
    subs=yaml.safe_load(path.read_text())['segments'][0]['subsegments']
    assert [s[0] for s in subs]==[0,0x20,0x40,0x58,0x80,0x98]


def test_a_candidate_limit_is_reported_without_changing_targets(tmp_path,monkeypatch):
    path,console=project(tmp_path);before=path.read_bytes()
    monkeypatch.setattr(mixed_code,'MAX_CANDIDATES',0)
    assert not mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    assert path.read_bytes()==before
    assert any('pass limit' in note for note in json_notes(tmp_path))


def test_existing_switch_pieces_keep_their_original_unit(tmp_path):
    path,console=project(tmp_path)
    piece=tmp_path/'asm/nonmatchings/unit/piece.s'
    asm(piece,[0x03E00008,0],0x60)
    piece.write_text(piece.read_text().replace('glabel caller','jlabel L80001060'))
    before=path.read_bytes()
    assert not mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    assert path.read_bytes()==before


def test_a_processor_without_a_reader_does_not_refine_raw_output(tmp_path):
    assert not mixed_code.refine(tmp_path/'missing.yaml',tmp_path,'missing.bin',get('nds'))
