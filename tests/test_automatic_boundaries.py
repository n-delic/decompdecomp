"""Automatic import repair preserves bytes and declines independent or ambiguous entries."""
from dataclasses import replace
from pathlib import Path

import pytest
import rabbitizer

from decompnet import consoles
from decompnet.rom.automatic_boundaries import repair
from decompnet.rom.split import AsmLine, DataSymbol, RomFunction, Segment, function_asm

BASE=0x80001000
BODY=[0x27bdffe8,0xafbf0014,0x10000001,0,0x8fbf0014,0,0x03e00008,0x27bd0018]


def fixture(words=BODY,cuts=(0,12,32),order='big'):
    image=b''.join(w.to_bytes(4,order) for w in words)
    functions=[]
    for lo,hi in zip(cuts,cuts[1:]):
        lines=[]
        for at in range(lo,hi,4):
            ins=rabbitizer.Instruction(words[at//4],BASE+at)
            text=ins.disassemble().split(None,1)
            lines.append(AsmLine(at,BASE+at,words[at//4],ins.getOpcodeName(),text[1] if len(text)>1 else ''))
        functions.append(RomFunction(f'func_{BASE+lo:08X}',BASE+lo,lo,'code',lines,Path()))
    return image,functions,[Segment('code',0,len(image),BASE)]


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_import_joins_generated_fragments_for_each_mips_console(platform):
    console=consoles.get(platform)
    image,functions,segments=fixture(order='big' if console.big_endian else 'little')
    original=b''.join(line.word.to_bytes(4,'big' if console.big_endian else 'little') for fn in functions for line in fn.lines)
    result=repair(functions,[],image,console,segments)
    assert [(f.vram,f.size) for f in result]==[(BASE,len(image))]
    assert b''.join(line.word.to_bytes(4,'big' if console.big_endian else 'little') for line in result[0].lines)==original
    assert BASE+12 in result[0].labels
    assert functions[0].size==12  # input disassembly is never mutated


def test_internal_fragment_references_become_local_labels_and_delay_overlap_is_deduplicated():
    image,functions,segments=fixture(cuts=(0,16,32))
    functions[0].lines[2]=replace(functions[0].lines[2],operands='func_80001010')
    functions[0].lines.append(functions[1].lines[0])
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert len(result)==1 and result[0].size==len(image)
    assert '.L80001010' in function_asm(result[0])


def test_balanced_interior_prologue_splits_a_merged_entry_without_losing_the_tail():
    words=BODY+BODY
    image,functions,segments=fixture(words,cuts=(0,12,44,64))
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.rom,f.size) for f in result]==[(0,32),(32,32)]
    assert [line.word for fn in result for line in fn.lines]==words


@pytest.mark.parametrize('protection',['symbol','pointer','call','branch','data','mapping','bad-bytes','gap','unclosed'])
def test_uncertain_or_independently_referenced_boundaries_are_unchanged(protection):
    image,functions,segments=fixture()
    data=[];symbols=[]
    if protection=='symbol':symbols=['entry = 0x8000100C; // type:func']
    if protection=='pointer':image+=(BASE+12).to_bytes(4,'big')
    if protection in ('call','branch'):
        word=(0x0c000000|((BASE+12)>>2)&0x3ffffff) if protection=='call' else 0x1000fffa
        # At BASE+32, the branch targets BASE+12.
        lines=[AsmLine(32,BASE+32,word,'jal' if protection=='call' else 'b','')]
        functions.append(RomFunction('caller',BASE+32,32,'other',lines,Path()))
        image+=bytes(32-len(image))+word.to_bytes(4,'big')
    if protection=='data':data=[DataSymbol('explicit_data',BASE+12,12,4,'data')]
    if protection=='mapping':segments.append(Segment('overlay',100,128,BASE))
    if protection=='bad-bytes':image=bytes(4)+image[4:]
    if protection=='gap':functions[0].lines.pop()
    if protection=='unclosed':
        words=BODY.copy();words[4]=0x08000c00
        image,functions,segments=fixture(words)
    result=repair(functions,data,image,consoles.get('n64'),segments,symbols=symbols)
    assert result==functions


def test_non_mips_projects_are_unchanged():
    image,functions,segments=fixture()
    assert repair(functions,[],image,consoles.get('gc'),segments)==functions


def test_repair_is_idempotent_and_does_not_promote_unrelated_switch_pieces():
    image,functions,segments=fixture(BODY+[0x03e00008,0],cuts=(0,12,32,40))
    functions[-1].piece=True
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert len(result)==2 and result[-1].piece
    assert repair(result,[],image,consoles.get('n64'),segments)==result


def test_the_shared_split_pipeline_runs_automatic_repair_before_returning_targets(tmp_path,monkeypatch):
    import yaml

    from decompnet.rom import mixed_code, split
    console=consoles.get('n64')
    image,functions,_=fixture()
    (tmp_path/'rom.z64').write_bytes(image)
    cfg={'name':'Synthetic','options':{'platform':'n64','compiler':'KMC','target_path':'rom.z64'},
         'segments':[{'name':'code','type':'code','start':0,'vram':BASE,'subsegments':[[0,'c','code']]},[len(image)]]}
    (tmp_path/'game.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
    folder=tmp_path/'asm/nonmatchings/code';folder.mkdir(parents=True)
    for fn in functions:(folder/(fn.name+'.s')).write_text(function_asm(fn))
    monkeypatch.setattr(split,'_splat',lambda *args,**kwargs:'')
    monkeypatch.setattr(mixed_code,'run',lambda *args,**kwargs:'')
    monkeypatch.setattr(console,'add_code',lambda config,workdir,image,picked,log:image)
    monkeypatch.setattr(console,'find_gp',lambda *args:0)
    result=split.split_rom(tmp_path,console=console,log=lambda _:None)
    assert [(fn.vram,fn.size) for fn in result.functions]==[(BASE,len(image))]


def test_a_local_label_moved_to_a_new_body_remains_linkable_from_its_caller():
    call=0x0c000000|((BASE+32)>>2)&0x3ffffff
    image,functions,segments=fixture(BODY+BODY+[call,0,0x03e00008,0],cuts=(0,12,44,64,80))
    functions[-1].lines[0]=replace(functions[-1].lines[0],operands='.L80001020')
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.rom,f.size) for f in result]==[(0,32),(32,32),(64,16)]
    assert result[-1].lines[0].operands=='func_80001020'
    assert functions[-1].lines[0].operands=='.L80001020'


def test_frameless_bodies_after_a_known_return_are_separated_through_their_delay_slots():
    leaf=[0x8c820000,0x24420001,0xac820000,0x03e00008,0]
    words=[0x03e00008,0]+[0]*3+leaf+leaf
    image,functions,segments=fixture(words,cuts=(0,8,28,60))
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.rom,f.size) for f in result]==[(0,8),(20,20),(40,20)]
    assert image[8:20]==bytes(12)  # original alignment remains in the image, outside C targets
    assert result[1].lines[-1].word==0 and result[2].lines[-2].word==0x03e00008


@pytest.mark.parametrize('word',[0x0c000400,0x27bdffe8,0x24100001,0x40806000,0x4484a000,0xc4940000])
def test_frameless_repair_rejects_calls_frames_saved_register_writes_and_hardware(word):
    from decompnet.arch.mips_code_recovery import leaf_extent
    words=[0x8c820000,word,0xac820000,0x03e00008,0]
    image,_,_=fixture(words,cuts=(0,len(words)*4))
    assert leaf_extent(image,0,len(image),BASE) is None


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_leaf_repair_is_shared_and_idempotent(platform):
    console=consoles.get(platform)
    words=[0x03e00008,0]+[0]*3+[0x8c820000,0x24420001,0xac820000,0x03e00008,0]
    image,functions,segments=fixture(words,cuts=(0,8,28,40),order='big' if console.big_endian else 'little')
    result=repair(functions,[],image,console,segments)
    assert [(f.rom,f.size) for f in result]==[(0,8),(20,20)]
    assert repair(result,[],image,console,segments)==result
