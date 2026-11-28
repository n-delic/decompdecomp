"""Dropped .word text is recovered through direct calls, not arbitrary instruction patterns."""
import struct

import pytest
import yaml

from decompnet.arch.mips_code_recovery import called_extent
from decompnet.consoles import get
from decompnet.rom import mixed_code
from decompnet.rom.split import Segment

BASE=0x80001000
BODY=[0x27BDFFE8,0xAFBF0014,0x24020007,0x8FBF0014,0x03E00008,0x27BD0018]


def packed(words,big=True):
    return struct.pack(('>' if big else '<')+f'{len(words)}I',*words)


def test_long_scheduled_prefix_is_kept_only_without_an_interior_entry():
    lead = [0x24020001]*13
    image = packed([0x03e00008, 0, *lead, *BODY])
    frame = 8+4*len(lead)
    segment = Segment('unit', 0, len(image), BASE)
    row = (8, len(image), segment, {}, [0, 'c', 'unit'])
    console = get('n64')
    size = called_extent(image, frame, len(image), BASE+frame, cpu=console.cpu_variant)
    assert size == len(BODY)*4
    args = (image, frame, size, row)
    assert mixed_code._scheduled_prefix(*args, set(), set(), called_extent, console) == (8, len(image)-8)
    assert mixed_code._scheduled_prefix(*args, {(frame, BASE+frame, 0)}, set(), called_extent, console) == (frame, size)
    assert mixed_code._scheduled_prefix(*args, set(), {BASE+frame}, called_extent, console) == (frame, size)


@pytest.mark.parametrize('big', [True, False])
def test_a_branch_delay_slot_cannot_become_a_separate_source_unit(tmp_path, big):
    path, console = project(tmp_path, big=big)
    image = bytearray((tmp_path/'rom.bin').read_bytes())
    # The frame is the delay slot of a branch into its own body.
    image[0x3c:0x40] = packed([0x14400003], big)
    (tmp_path/'rom.bin').write_bytes(image)
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s',
        list(struct.unpack(('>' if big else '<')+f'{(len(image)-32)//4}I', image[32:])), 32, raw=True, big=big)
    assert not mixed_code.refine(path, tmp_path, 'rom.bin', console, log=lambda _: None)


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


def project(tmp_path,*,body=BODY,call=True,pointer=False,explicit=False,overlap=False,big=True,console_id=None):
    console=get(console_id or ('n64' if big else 'ps'))
    caller=[0x27BDFFE8,0xAFBF0014,0x0C000000|((BASE+0x40)>>2)&0x3FFFFFF,0,
            0x8FBF0014,0x27BD0018,0x03E00008,0]
    if not call:caller[2]=0
    if pointer:caller[2:4]=[0x3C048000|BASE>>16&0,0x24841000|(BASE+0x40)&0xFFFF]
    words=caller+[0x7000003F]*8+body+[0]*10
    rom=packed(words,big);(tmp_path/'rom.bin').write_bytes(rom)
    cfg={'options':{},'segments':[{'name':'unit','type':'code','start':0,'vram':BASE,
         'subsegments':[[0,'c','unit']]+([[32,'data','data']] if explicit else [])},[len(rom)]]}
    if overlap:cfg['segments'].insert(-1,{'name':'overlay','type':'code','start':len(rom)-4,'vram':BASE+0x40,
                                        'subsegments':[[len(rom)-4,'c','overlay']]})
    path=tmp_path/'game.yaml';path.write_text(yaml.safe_dump(cfg,sort_keys=False))
    asm(tmp_path/'asm/nonmatchings/unit/caller.s',caller,0,big=big)
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s',words[8:],32,raw=True,big=big)
    return path,console


def test_refine_keeps_a_long_scheduled_prefix_with_its_frame(tmp_path):
    path, console = project(tmp_path)
    image = bytearray((tmp_path/'rom.bin').read_bytes())
    image.extend(bytes(0xa0-len(image)))
    image[0x38:0x40] = packed([0x03e00008, 0])
    image[0x40:0x74] = packed([0x24020001]*13)
    body = [0x27bdffe8, 0xafbf0014, 0x0c000000 | ((BASE >> 2) & 0x3ffffff),
            0, 0x8fbf0014, 0x03e00008, 0x27bd0018]
    image[0x74:0x90] = packed(body)
    (tmp_path/'rom.bin').write_bytes(image)
    cfg = yaml.safe_load(path.read_text())
    cfg['segments'][-1] = [len(image)]
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s', list(struct.unpack('>32I', image[32:])), 32, raw=True)
    assert mixed_code.refine(path, tmp_path, 'rom.bin', console, log=lambda _: None)
    cuts = [part[0] for part in yaml.safe_load(path.read_text())['segments'][0]['subsegments']]
    assert 0x40 in cuts and 0x90 in cuts and 0x74 not in cuts


@pytest.mark.parametrize('reference', ['none', 'call', 'branch', 'pointer'])
def test_refine_keeps_unreferenced_call_continuations_in_one_unit(tmp_path, reference):
    path, console = project(tmp_path)
    image = bytearray((tmp_path/'rom.bin').read_bytes())
    call = 0x0c000000 | ((BASE+0x40) >> 2 & 0x3ffffff)
    image[0x18:0x20] = packed([call, 0])
    image[0x20:0x28] = packed([0x24040001, 0x24050002])
    if reference == 'call':
        image[0x10:0x14] = packed([0x0c000000 | ((BASE+0x20) >> 2 & 0x3ffffff)])
    if reference == 'branch':
        image[0x10:0x14] = packed([0x10000003])
    if reference == 'pointer':
        image[-4:] = packed([BASE+0x20])
    (tmp_path/'rom.bin').write_bytes(image)
    asm(tmp_path/'asm/nonmatchings/unit/caller.s', list(struct.unpack('>8I', image[:0x20])), 0)
    asm(tmp_path/'asm/nonmatchings/unit/continuation.s', list(struct.unpack('>2I', image[0x20:0x28])), 0x20)
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s', list(struct.unpack(f'>{(len(image)-0x28)//4}I', image[0x28:])), 0x28, raw=True)
    assert mixed_code.refine(path, tmp_path, 'rom.bin', console, log=lambda _: None)
    cuts = [part[0] for part in yaml.safe_load(path.read_text())['segments'][0]['subsegments']]
    assert (0x20 in cuts) == (reference != 'none')


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


def test_lost_overlay_entries_are_anchored_in_their_own_bank(tmp_path):
    config = {'segments': [
        {'name': 'first', 'type': 'code', 'start': 0x100, 'vram': BASE,
         'exclusive_ram_id': 'slot', 'subsegments': [[0x100, 'c', 'first']]},
        {'name': 'second', 'type': 'code', 'start': 0x200, 'vram': BASE,
         'exclusive_ram_id': 'slot', 'subsegments': [[0x200, 'c', 'second'], [0x280, 'data', 'data']]},
        [0x300]]}
    path = tmp_path/'game.yaml'
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    missing = {(0x220, BASE+0x20, 0x27bdffe8)}
    assert mixed_code._anchor_lost_overlay_entries(path, missing, {next(iter(missing)): 0x18})
    result = yaml.safe_load(path.read_text())['segments']
    assert result[0] == config['segments'][0]
    assert [(s[0], s[1]) for s in result[1]['subsegments']] == [
        (0x200, 'c'), (0x220, 'c'), (0x238, 'c'), (0x280, 'data')]

    # An entry crossing explicit data cannot be made safe by changing its bank.
    before = path.read_bytes()
    crossing = {(0x278, BASE+0x78, 0x27bdffe8)}
    assert not mixed_code._anchor_lost_overlay_entries(path, crossing, {next(iter(crossing)): 0x10})
    assert path.read_bytes() == before


@pytest.mark.parametrize('retry_restores', [True, False])
def test_overlay_retry_keeps_only_a_split_that_preserves_all_entries(tmp_path, monkeypatch, retry_restores):
    from decompnet.rom import split

    path = tmp_path/'game.yaml'
    initial = {'segments': [{'name': 'bank', 'type': 'code', 'start': 0x100, 'vram': BASE,
                            'exclusive_ram_id': 'slot', 'subsegments': [[0x100, 'c', 'bank']]}, [0x200]]}
    path.write_text(yaml.safe_dump(initial, sort_keys=False))
    (tmp_path/'asm').mkdir()
    key = (0x120, BASE+0x20, 0x27bdffe8)
    def refine(*args, **kwargs):
        monkeypatch.setattr(mixed_code, 'refine', lambda *a, **k: False)
        return True
    monkeypatch.setattr(mixed_code, 'refine', refine)
    monkeypatch.setattr(mixed_code, '_detected_functions', lambda *args: {key: 0x18})
    passes = []
    def splat(args, root, log, **kwargs):
        passes.append(yaml.safe_load(path.read_text()))
        (root/'asm').mkdir()
        return ''
    monkeypatch.setattr(split, '_splat', splat)
    monkeypatch.setattr(mixed_code, 'detected_entries',
                        lambda *args: {key} if retry_restores and len(passes) == 2 else set())
    mixed_code.run(path, tmp_path, 'rom.bin', get('n64'), log=lambda *_: None)
    assert len(passes) == (2 if retry_restores else 3)
    assert any(s[0] == 0x120 for s in passes[1]['segments'][0]['subsegments'])
    if not retry_restores:
        assert yaml.safe_load(path.read_text()) == initial


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
    assert any('Recovered 1 framed entries with call evidence' in note for note in result.coverage_notes)
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


def test_a_call_inside_a_declared_slot_can_recover_its_own_bank(tmp_path):
    path,console = project(tmp_path,overlap=True)
    cfg = yaml.safe_load(path.read_text())
    for bank in cfg['segments'][:-1]:
        bank['exclusive_ram_id']='slot'
    path.write_text(yaml.safe_dump(cfg))
    raw = tmp_path/'asm/nonmatchings/unit/dropped.s'
    lines = raw.read_text().splitlines()
    raw.write_text('\n'.join(lines[:-1])+'\n')
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    assert any(s[0]==64 for s in yaml.safe_load(path.read_text())['segments'][0]['subsegments'])


def test_a_body_reached_only_through_a_built_pointer_is_recovered_too(tmp_path):
    """A callback or handler-table entry has no jal; the address its owner builds is the same independent evidence."""
    path,console=project(tmp_path,call=False,pointer=True)
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    subs=yaml.safe_load(path.read_text())['segments'][0]['subsegments']
    assert [s[0] for s in subs]==[0,0x20,0x40,0x58]


def test_a_quiet_body_that_calls_a_known_function_is_recovered_but_a_leaf_like_table_is_not(tmp_path):
    """Nothing calls or points at it; the $ra spill/restore and a call into the project's own code corroborate it."""
    quiet=[0x27BDFFE8,0xAFBF0014,0x0C000000|(BASE>>2)&0x3FFFFFF,0,0x8FBF0014,0x03E00008,0x27BD0018]
    path,console=project(tmp_path,body=quiet,call=False)
    assert mixed_code.refine(path,tmp_path,'rom.bin',console,log=lambda _:None)
    subs=yaml.safe_load(path.read_text())['segments'][0]['subsegments']
    assert 0x40 in [s[0] for s in subs]
    other=tmp_path/'b';other.mkdir()
    path,console=project(other,call=False)  # BODY calls nothing: stays raw
    assert not mixed_code.refine(path,other,'rom.bin',console,log=lambda _:None)


def test_find_missed_functions_scans_inside_code_segments_given_the_known_functions(tmp_path, monkeypatch):
    """The button found nothing inside a code segment (only gaps between segments): a quiet body in the raw bytes
    between the project's own functions now gets its own subsegment, so its project gains it without a reimport."""
    from decompnet.consoles.n64 import runtime_staging
    from decompnet.consoles.n64.code import missed_functions
    # This fixture exercises candidate discovery and cutting; its minimal config is
    # not a splat project. Staging's real split checks have their own integration tests.
    monkeypatch.setattr(runtime_staging, 'verify', lambda *args, **kwargs: True)
    quiet=[0x27BDFFE8,0xAFBF0014,0x0C000000|(BASE>>2)&0x3FFFFFF,0,0x8FBF0014,0x03E00008,0x27BD0018]
    path,_=project(tmp_path,body=quiet,call=False)
    rom=(tmp_path/'rom.bin').read_bytes()
    caller=(0,0x20,BASE)
    assert missed_functions(path,rom,lambda _:None)==[]
    found=missed_functions(path,rom,lambda _:None,[caller])
    assert (0x40,0x5C,BASE+0x40) in found
    subs=yaml.safe_load(path.read_text())['segments'][0]['subsegments']
    assert 0x40 in [s[0] for s in subs]


def test_anchor_does_not_create_a_source_unit_on_unentered_nop(tmp_path):
    path, console = project(tmp_path)
    image = bytearray((tmp_path/'rom.bin').read_bytes())
    image[0x20:0x24] = bytes(4)
    (tmp_path/'rom.bin').write_bytes(image)
    asm(tmp_path/'asm/nonmatchings/unit/dropped.s',
        list(struct.unpack('>24I', image[0x20:])), 0x20, raw=True)
    assert mixed_code.refine(path, tmp_path, 'rom.bin', console, log=lambda _: None)
    starts = [part[0] for part in yaml.safe_load(path.read_text())['segments'][0]['subsegments']]
    assert 0x20 not in starts
    assert 0x40 in starts


def test_quiet_long_frame_can_call_an_unambiguous_other_segment(tmp_path):
    caller = [0x27BDFFE8, 0xAFBF0014, 0x24020001, 0, 0x8FBF0014, 0x03E00008, 0x27BD0018, 0]
    body = [0x27BDFFE8, 0xAFBF0014, 0x0C000000 | ((BASE >> 2) & 0x3FFFFFF), 0]
    body += [0x24020001] * 300
    body += [0x8FBF0014, 0x03E00008, 0x27BD0018]
    words = caller + [0x7000003F] * 8 + body + [0] * 4
    (tmp_path/'rom.bin').write_bytes(packed(words))
    cfg = {'options': {}, 'segments': [
        {'name': 'caller', 'type': 'code', 'start': 0, 'vram': BASE, 'subsegments': [[0, 'c', 'caller']]},
        {'name': 'unit', 'type': 'code', 'start': 32, 'vram': BASE+0x1000, 'subsegments': [[32, 'c', 'unit']]},
        [len(words)*4]]}
    path = tmp_path/'game.yaml'; path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    asm(tmp_path/'asm/nonmatchings/caller/caller.s', caller, 0)
    raw = tmp_path/'asm/nonmatchings/unit/dropped.s'
    asm(raw, words[8:], 32, raw=True)
    text = raw.read_text()
    for offset in reversed(range(32, len(words)*4, 4)):
        text = text.replace(f'{BASE+offset:08X}', f'{BASE+0x1000+offset-32:08X}')
    raw.write_text(text)
    assert mixed_code.refine(path, tmp_path, 'rom.bin', get('n64'), log=lambda _: None)
    cuts = [x[0] for x in yaml.safe_load(path.read_text())['segments'][1]['subsegments']]
    assert 0x40 in cuts and 0x40+len(body)*4 in cuts
