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


@pytest.mark.parametrize('case',['scheduled','different argument','different callee','incoming pointer'])
def test_exception_cleanup_delay_slot_can_precede_two_alignment_words(case):
    entry = BASE+32
    target = entry+4 if case=='different callee' else entry
    call = 0x0c000000 | ((target>>2)&0x3ffffff)
    delay = 0x00002825 if case=='different argument' else 0x00002025
    words = [0x40026000,0,0,0x42000018,call,delay,0,0]+BODY
    image,functions,segments=fixture(words,cuts=(0,24,28,32,64))
    del functions[2]  # the second padding word was already trimmed by the disassembler
    if case=='incoming pointer':
        image+=(BASE+24).to_bytes(4,'big')
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(fn.rom,fn.size) for fn in result] == (
        [(0,24),(32,32)] if case=='scheduled' else [(0,24),(24,4),(32,32)])


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


FILL=0x331f630f  # leftover bytes an assembler wrote as 8-byte alignment (decodes as andi $ra, ...)


@pytest.mark.parametrize('case',['filler','symbol','pointer','8-aligned','no return before','no entry after'])
def test_one_alignment_word_between_a_return_and_the_next_entry_is_left_out(case):
    before=[0x8c820000,0x8c820000,0x03e00008,0] if case=='8-aligned' else [0x8c820000,0x8c820000,0x8c820000] if case=='no return before' else [0x8c820000,0x03e00008,0]
    after=[0x8c820000,0x03e00008,0,0] if case=='no entry after' else BODY
    words=before+[FILL]+after;cut=4*len(before)
    if case=='pointer':words+=[BASE+cut]
    image,functions,segments=fixture(words,cuts=(0,cut,cut+4,cut+4+4*len(after))+((len(words)*4,) if case=='pointer' else ()))
    symbols=[f'func_{BASE+cut:08X} = 0x{BASE+cut:08X};'] if case=='symbol' else ()
    result=repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    if case=='filler':
        assert [(f.rom,f.size) for f in result]==[(0,12),(16,32)]
        assert image[12:16]==FILL.to_bytes(4,'big')  # the word stays in the image, outside any function
        assert repair(result,[],image,consoles.get('n64'),segments)==result
    else:
        assert any(f.rom==cut for f in result)


@pytest.mark.parametrize('filler',[0x0ed4f03f,0x100ceedd])  # decodes as jal / beq: the next entry's first word became its delay slot
def test_alignment_filler_that_decodes_as_a_jump_is_left_out_with_its_borrowed_delay_slot(filler):
    before=[0x8c820000,0x03e00008,0];words=before+[filler]+BODY;cut=4*len(before)
    image,functions,segments=fixture(words,cuts=(0,cut,cut+4,len(words)*4))
    functions[1].lines.append(functions[2].lines[0])  # spimdisasm's 8-byte entry, overlapping the next one
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.rom,f.size) for f in result]==[(0,12),(16,32)]
    assert repair(result,[],image,consoles.get('n64'),segments)==result


def test_an_eight_byte_entry_is_kept_when_its_first_word_needs_no_delay_slot():
    before=[0x8c820000,0x03e00008,0];words=before+[FILL]+BODY;cut=4*len(before)
    image,functions,segments=fixture(words,cuts=(0,cut,cut+4,len(words)*4))
    functions[1].lines.append(functions[2].lines[0])
    assert any(f.rom==cut for f in repair(functions,[],image,consoles.get('n64'),segments))


def shared_slot(callers):
    """BODY cut at BASE+12 by calls from another segment, `callers` more of which land on its every word (a sibling
    overlay's functions), with one call to the real entry."""
    image,functions,segments=fixture()
    lines=[]
    targets=[BASE+12]+[BASE+4*(1+i%7) for i in range(callers)]
    caller=0x1000
    image+=bytes(caller-len(image))
    for i,target in enumerate([BASE]+targets):
        word=0x0c000000|(target>>2)&0x3ffffff
        lines.append(AsmLine(caller+4*i,0x80100000+4*i,word,'jal',''))
        image+=word.to_bytes(4,'big')
    functions.append(RomFunction('caller',0x80100000,caller,'main',lines,Path()))
    segments.append(Segment('main',caller,len(image),0x80100000))
    return image,functions,segments


def test_calls_into_a_shared_overlay_slot_prove_no_entry():
    """An N64 game loads five overlays at one address and main calls all of them: most calls land mid-function in
    the one image the split mapped there, and none of them proves an entry, so its fragments join."""
    image,functions,segments=shared_slot(callers=12)
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.vram,f.size) for f in result if f.segment=='code']==[(BASE,32)]


def test_a_few_outside_calls_keep_an_ordinary_overlays_entries():
    image,functions,segments=shared_slot(callers=0)
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.vram,f.size) for f in result if f.segment=='code']==[(BASE,12),(BASE+12,20)]


def test_a_zero_word_the_disassembler_left_between_pieces_joins_with_them():
    """spimdisasm ends a piece before a nop it takes for alignment, so the next piece starts 4 bytes on: an N64 game's
    overlay had a dozen such holes, each splitting its pieces into groups that could not be joined across."""
    words=BODY[:3]+[0]+BODY[4:]
    image,functions,segments=fixture(words,cuts=(0,12,32))
    functions[1]=replace(functions[1],vram=BASE+16,rom=16,lines=functions[1].lines[1:])
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.vram,f.size) for f in result]==[(BASE,32)]
    assert [line.word for line in result[0].lines]==words


def test_an_alignment_hole_stays_out_of_pieces_that_do_not_join():
    words=BODY[:3]+[0]+BODY[4:]
    words[4]=0x08000c00  # the second piece never closes: nothing joins
    image,functions,segments=fixture(words,cuts=(0,12,32))
    functions[1]=replace(functions[1],vram=BASE+16,rom=16,lines=functions[1].lines[1:])
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.vram,f.size) for f in result]==[(BASE,12),(BASE+16,16)]


def test_a_piece_whose_frame_comes_after_scheduled_loads_joins_its_tail():
    words=[0x03e00008,0,0x3c028010,0x904202a0]+BODY  # a return, then loads ahead of the next frame
    image,functions,segments=fixture(words,cuts=(0,8,24,48))
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(f.vram,f.size) for f in result]==[(BASE,8),(BASE+8,40)]


@pytest.mark.parametrize('independent_call', [False, True])
def test_pc_relative_halfword_table_is_data_unless_called(independent_call):
    words = [
        0x03e00821, 0x04100008, 0, 0x27e8001c, 0x0020f821,
        0x01064021, 0x01064021, 0x95080000, 0x03e00008, 0,
        0x12345678, 0x8abcdef0,
        0x03e00008, 0,
        0x0c000000 | ((BASE + (40 if independent_call else 48)) >> 2 & 0x3ffffff),
        0x0c000000 | ((BASE + 48) >> 2 & 0x3ffffff), 0x03e00008, 0,
    ]
    image, functions, segments = fixture(words, (0, 40, 48, 56, 72))
    data = []
    result = repair(functions, data, image, consoles.get('n64'), segments)
    assert (BASE + 40 in {fn.vram for fn in result}) == independent_call
    assert [(symbol.vram, symbol.rom, symbol.size) for symbol in data] == (
        [] if independent_call else [(BASE + 40, 40, 8)]
    )


def test_pc_relative_byte_table_is_data():
    words = [
        0x03e00821, 0x04100008, 0, 0x27ef001c, 0x0020f821,
        0x01e47821, 0x81e10000, 0, 0x03e00008, 0,
        0x12345678, 0x8abcdef0,
        0x03e00008, 0,
        0x0c000000 | ((BASE + 48) >> 2 & 0x3ffffff), 0, 0x03e00008, 0,
    ]
    image, functions, segments = fixture(words, (0, 40, 48, 56, 72))
    data = []
    result = repair(functions, data, image, consoles.get('n64'), segments)
    assert BASE + 40 not in {fn.vram for fn in result}
    assert [(symbol.vram, symbol.size) for symbol in data] == [(BASE + 40, 8)]


@pytest.mark.parametrize('referenced', [False, True])
def test_overlay_end_marker_is_data_only_without_entry_evidence(referenced):
    words = [0, 0, 0x09000432, 0x40822000, 0, 0, 0x2400beef] + [0] * 7 + [0x03e00008, 0]
    # Place the marker on a 16-byte VRAM boundary, with a trimmed zero gap after the tail jump.
    image, functions, segments = fixture(words, (0, 16, 24, 28, 56, 64))
    functions = [functions[i] for i in (0, 2, 4)]
    for fn in functions:
        fn.vram += 8
        fn.name = f'func_{fn.vram:08X}'
        for line in fn.lines:
            line.vram += 8
    segments[0].vram += 8
    marker = functions[1]
    symbols = [f'{marker.name} = 0x{marker.vram:08X}; // type:func'] if referenced else []
    data = []
    result = repair(functions, data, image, consoles.get('n64'), segments, symbols=symbols)
    assert (marker.vram in {fn.vram for fn in result}) == referenced
    assert [(symbol.vram, symbol.rom, symbol.size) for symbol in data] == (
        [] if referenced else [(marker.vram, marker.rom, 4)]
    )


def test_nop_padding_emitted_in_a_separate_source_unit_is_omitted():
    image, functions, segments = fixture(words=[*BODY, 0, 0, *BODY], cuts=(0, 32, 36, 40, 72))
    functions[1].segment = 'new_source_unit'
    functions[2].segment = 'new_source_unit'
    console = consoles.get('n64')
    result = repair(functions, [], image, console, segments)
    assert [f.rom for f in result] == [0, 40]
    result = repair(functions, [], image, console, segments, symbols=[f'keep = 0x{BASE+36:08X};'])
    assert sorted(f.rom for f in result) == [0, 32, 36, 40]


def test_directly_called_leaf_recovers_return_in_the_next_fragment():
    target = BASE+12
    call = 0x0c000000 | (target>>2)&0x3ffffff
    words = [call,0,0x8c820000,0x8c82002c,0x03e00008,0]
    image,functions,segments = fixture(words,cuts=(0,12,16,24))
    result = repair(functions,[],image,consoles.get('n64'),segments)
    assert [(fn.rom,fn.size) for fn in result] == [(0,12),(12,12)]


def test_generated_source_units_do_not_interrupt_a_single_mapped_function():
    image,functions,segments = fixture()
    functions[1] = replace(functions[1],segment='generated_next_unit')
    result = repair(functions,[],image,consoles.get('n64'),segments)
    assert [(fn.segment,fn.rom,fn.size) for fn in result] == [('code',0,32)]


@pytest.mark.parametrize('pointer',[None,16,20])
def test_long_zero_padding_preserves_words_at_and_after_an_interior_pointer(pointer):
    entry = BASE+64
    words = [0x0c000000 | ((entry>>2)&0x3ffffff),0,0x03e00008,0]+[0]*12+BODY
    image,functions,segments = fixture(words,cuts=(0,16,20,24,64,96))
    functions.pop(3)
    if pointer:
        image += (BASE+pointer).to_bytes(4,'big')
    result = repair(functions,[],image,consoles.get('n64'),segments)
    assert any(fn.rom==16 for fn in result) == (pointer==16)
    assert any(fn.rom==20 for fn in result) == (pointer is not None)


@pytest.mark.parametrize('called',[False,True])
def test_single_zero_after_a_closed_back_edge_requires_a_called_next_entry(called):
    entry = BASE+16
    caller = 0x0c000000 | ((entry>>2)&0x3ffffff) if called else 0
    words = [0x8c820000,0x1000fffe,0,0,0x8c820000,0x03e00008,0,caller,0,0x03e00008,0]
    image,functions,segments = fixture(words,cuts=(0,12,16,28,44))
    result = repair(functions,[],image,consoles.get('n64'),segments)
    assert any(fn.rom <= 12 < fn.rom+fn.size for fn in result) != called


@pytest.mark.parametrize('case',['valid','base_clobber','explicit_entry','external_case'])
def test_segment_prefix_with_saved_register_switch_is_recovered_as_data(case):
    # The dispatch uses a table base initialized before a call in a preserved register.
    start = BASE+20
    first, default = start+44,start+52
    prefix = [0x54455354,0,0,first,default if case!='external_case' else BASE+0x300]
    call = 0x0c000000 | (((BASE+0x200)>>2)&0x3ffffff)
    code = [0x3c158000,0x26b5100c,call,0 if case!='base_clobber' else 0x24150000,
            0x2c620002,0x10400007,0x00031080,0x00551021,0x8c420000,0x00400008,0,
            0x03e00008,0,0x03e00008,0]
    image,functions,segments = fixture(prefix+code,cuts=(0,20,80))
    functions[1].lines[0] = replace(functions[1].lines[0],
                                      operands=f'$s5, %hi({functions[0].name} + 12)')
    symbols=[f'entry = 0x{BASE:08X}; // type:func'] if case=='explicit_entry' else ()
    data=[]
    result=repair(functions,data,image,consoles.get('n64'),segments,symbols=symbols)
    if case=='valid':
        assert [(fn.rom,fn.size) for fn in result]==[(20,60)]
        assert [(row.rom,row.size,row.kind) for row in data]==[(0,12,'data'),(12,8,'jtbl')]
    else:
        assert result==functions
        assert not data


@pytest.mark.parametrize('protected',[False,True])
def test_alignment_word_decoding_as_conditional_link_does_not_cut_a_leaf(protected):
    words=[0x8c820000,0x03e00008,0,0x04330002,0x24840001,0x24820001,0x03e00008,0]
    image,functions,segments=fixture(words,cuts=(0,12,24,32))
    symbols=[f'entry = 0x{BASE+12:08X}; // type:func'] if protected else ()
    result=repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    if protected:
        assert result==functions
    else:
        assert [(fn.rom,fn.size) for fn in result]==[(0,12),(16,16)]
        assert repair(result,[],image,consoles.get('n64'),segments)==result


@pytest.mark.parametrize('case',['filler','explicit','code_follows','no_float_table'])
def test_terminal_alignment_before_unmapped_float_data(case):
    words=[0x8c820000,0x03e00008,0,0x530d0a0d]
    image,functions,segments=fixture(words,cuts=(0,12,16))
    import struct
    image += struct.pack('>8f',1,2,3,4,5,6,7,8) if case!='no_float_table' else bytes(32)
    symbols=[f'entry = 0x{BASE+12:08X}; // type:func'] if case=='explicit' else ()
    if case=='code_follows':
        segments.append(Segment('other',16,48,BASE+64))
    result=repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    assert any(fn.rom==12 for fn in result)==(case!='filler')


@pytest.mark.parametrize('case',['terminal table','slot outside code','explicit entry','missing table','mapped remainder'])
def test_terminal_call_fragment_cannot_borrow_the_first_memory_range_limit(case):
    words = [0x8c820000,0x03e00008,0,0x0d000000,0x7fffffff]
    image,functions,segments = fixture(words,cuts=(0,12,20))
    rest = [0,0x803fffff,3,0x9fffffff,0,0xa03fffff,3]
    if case=='missing table':
        rest[-1] = 4
    image += b''.join(word.to_bytes(4,'big') for word in rest)
    if case=='slot outside code':
        segments[0] = replace(segments[0],rom_end=16)
    if case=='mapped remainder':
        segments.append(Segment('other',20,48,BASE+64))
    symbols = [f'entry = 0x{BASE+12:08X}; // type:func'] if case=='explicit entry' else ()
    result = repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    assert any(fn.rom==12 for fn in result)==(case not in ('terminal table','slot outside code'))


def test_terminal_fp_branch_filler_can_borrow_a_word_from_the_float_table():
    import struct

    entry=[0x8c820000,0x03e00008,0]
    values=struct.pack('>8f',1,2,3,4,5,6,7,8)
    words=entry+[0x45000004]+[word for word, in struct.iter_unpack('>I',values)]
    image,functions,segments=fixture(words,cuts=(0,12,20,48))
    functions.pop()
    segments[0].rom_end=16
    assert [(fn.rom,fn.size) for fn in repair(functions,[],image,consoles.get('n64'),segments)]==[(0,12)]


@pytest.mark.parametrize('case',['load','nonzero','address_escape','branch'])
def test_word_access_to_zero_between_return_and_entry_is_recovered_as_data(case):
    # A plain LUI/LW pair references the retained zero word, never executes it.
    words=[0x8c820000,0x03e00008,0,0x100 if case=='nonzero' else 0]+BODY
    words += [0x3c028000,0x8c42100c if case!='address_escape' else 0x2442100c,
              0x1000fff4 if case=='branch' else 0x03e00008,0]
    image,functions,segments=fixture(words,cuts=(0,12,16,48,64))
    ref=functions[1].name
    caller=functions[-1]
    caller.lines[:2]=[replace(caller.lines[0],operands=f'$v0, %hi({ref})'),
                     replace(caller.lines[1],operands=f'$v0, %lo({ref})($v0)')]
    data=[]
    result=repair(functions,data,image,consoles.get('n64'),segments)
    if case in ('load','nonzero'):
        assert [(row.vram,row.rom,row.size) for row in data]==[(BASE+12,12,4)]
        assert all(fn.rom!=12 for fn in result)
    else:
        assert not data and any(fn.rom==12 for fn in result)


def test_raw_return_before_assembler_filler_needs_a_direct_call_to_the_next_entry():
    entry=BASE+16
    call=0x0c000000|((entry>>2)&0x3ffffff)
    for called in (False,True):
        words=[0x8c820000,0x03e00008,0,FILL]+BODY+[call if called else 0,0,0x03e00008,0]
        image,functions,segments=fixture(words,cuts=(0,12,16,48,64))
        functions.pop(0)
        result=repair(functions,[],image,consoles.get('n64'),segments)
        assert any(fn.rom==12 for fn in result) != called


@pytest.mark.parametrize('case',['closed','protected','exception_return'])
def test_called_closed_assembly_body_joins_generated_fragments(case):
    words=[0x20820001,0x10800003,0x42000018 if case=='exception_return' else 0,0x03e00008,0,0x03e00008,0,
           0x0c000000|((BASE>>2)&0x3ffffff),0,0x03e00008,0]
    image,functions,segments=fixture(words,cuts=(0,12,20,28,44))
    symbols=[f'entry = 0x{BASE+12:08X}; // type:func'] if case=='protected' else ()
    result=repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    assert [(fn.rom,fn.size) for fn in result]==(
        [(0,28),(28,16)] if case=='closed' else [(0,12),(12,8),(20,8),(28,16)])


def test_conditional_filler_cannot_create_an_entry_inside_a_called_leaf():
    entry=BASE+16
    call=0x0c000000|((entry>>2)&0x3ffffff)
    words=[0x8c820000,0x03e00008,0,0x14850002,0x24820001,0x24420001,0x03e00008,0,
           call,0,0x03e00008,0]
    image,functions,segments=fixture(words,cuts=(0,12,16,20,32,48))
    result=repair(functions,[],image,consoles.get('n64'),segments)
    assert [(fn.rom,fn.size) for fn in result]==[(0,12),(16,16),(32,16)]


@pytest.mark.parametrize('writer',[False,True])
def test_zero_address_passed_to_a_direct_setter_is_data(writer):
    setter=BASE+48
    call=0x0c000000|((setter>>2)&0x3ffffff)
    words=[0x8c820000,0x03e00008,0,0]+BODY
    words.extend([0x03e00008,0xac850000 if writer else 0x8c850000,
                  0x3c048000,call,0x2484100c,0x03e00008,0])
    image,functions,segments=fixture(words,cuts=(0,12,16,48,56,76))
    reference=functions[1].name
    caller=functions[-1]
    caller.lines[0]=replace(caller.lines[0],operands=f'$a0, %hi({reference})')
    caller.lines[2]=replace(caller.lines[2],operands=f'$a0, $a0, %lo({reference})')
    data=[]
    result=repair(functions,data,image,consoles.get('n64'),segments)
    assert any(fn.rom==12 for fn in result) != writer
    assert [(row.vram,row.rom,row.size) for row in data]==([(BASE+12,12,4)] if writer else [])


@pytest.mark.parametrize('protect', [False, True])
@pytest.mark.parametrize('fill', [0x00040000, 0x04000000])
def test_multiword_alignment_between_a_return_and_an_aligned_frame(protect, fill):
    words = [0x03e00008, 0, fill, 0x00000021] + BODY
    image, functions, segments = fixture(words, cuts=(0,8,16,len(words)*4))
    symbols = [f'entry = 0x{BASE+12:08X};'] if protect else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert [fn.rom for fn in result] == ([0,8,16] if protect else [0,16])


@pytest.mark.parametrize('called', [False, True])
def test_alignment_call_folded_into_the_next_frame(called):
    apparent_call = 0x0c000000 | (BASE >> 2)&0x3ffffff
    words = [0x03e00008, 0, apparent_call, 0] + BODY
    image, functions, segments = fixture(words, cuts=(0,8,len(words)*4))
    symbols = [f'entry = 0x{BASE+8:08X};'] if called else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert [fn.rom for fn in result] == [0,8 if called else 16]


@pytest.mark.parametrize('entered', [False, True])
def test_backward_branch_filler_before_a_called_aligned_leaf(entered):
    call = 0x0c000000 | (BASE+16 >> 2)&0x3ffffff
    words = [0x8c820000, 0x03e00008, 0, 0x1440fffc, 0x8c820000, 0x03e00008, 0, call, 0, 0x03e00008, 0]
    image, functions, segments = fixture(words, cuts=(0,12,16,28,44))
    symbols = [f'entry = 0x{BASE+12:08X};'] if entered else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert any(fn.rom == 12 for fn in result) == entered


def test_three_alignment_words_after_a_closed_back_edge():
    call = 0x0c000000 | (BASE+32 >> 2)&0x3ffffff
    words = [0x8c820000, 0x24420001, 0xac820000, 0x1000fffc, 0, 0, 0, 0,
             0x8c820000, 0x03e00008, 0, call, 0, 0x03e00008, 0]
    image, functions, segments = fixture(words, cuts=(0,20,24,28,32,44,60))
    result = repair(functions, [], image, consoles.get('n64'), segments)
    assert [fn.rom for fn in result] == [0,32,44]


def test_long_zero_prefix_before_disassembled_inline_data():
    words = [0x8c820000,0x03e00008,0]+[0]*17+[0x11111111]
    image, functions, segments = fixture(words,cuts=(0,12,16,len(words)*4))
    functions.pop()
    inline_data = {(80,BASE+80)}
    result = repair(functions,[],image,consoles.get('n64'),segments,inline_data=inline_data)
    assert [fn.rom for fn in result] == [0]
    assert [fn.rom for fn in repair(functions,[],image,consoles.get('n64'),segments)] == [0,12]


@pytest.mark.parametrize('case', ['padding', 'named', 'next_mapping'])
def test_unreachable_alignment_at_code_mapping_end(case):
    words = [0x8C820000, 0x03E00008, 0, 0x8E050074]
    image, functions, segments = fixture(words, cuts=(0, 12, 16))
    image += bytes(64)
    symbols = [f'entry = 0x{BASE+12:08X};'] if case == 'named' else ()
    if case == 'next_mapping':
        segments.append(Segment('other', 16, 80, BASE+64))
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert [fn.rom for fn in result] == ([0] if case == 'padding' else [0, 12])


def test_alignment_between_return_and_closed_frameless_entry():
    words = [0x03E00008, 0, 0x80FF80FF, 0x80FF80FF, 0x8C820000, 0x03E00008, 0]
    image, functions, segments = fixture(words, cuts=(0, 8, 16, 28))
    result = repair(functions, [], image, consoles.get('n64'), segments)
    assert [(fn.rom, fn.size) for fn in result] == [(0, 8), (16, 12)]


@pytest.mark.parametrize('protected', [False, True])
def test_alignment_exposed_by_recovering_the_preceding_body(protected):
    words = BODY[:-2]+[0x8C820000, 0x03E00008, 0x27BD0018]+[0x8C820000, 0x80FF80FF, 0]+BODY
    image, functions, segments = fixture(words, cuts=(0, 12, 48, 80))
    symbols = [f'entry = 0x{BASE+36:08X};'] if protected else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert any(fn.rom <= 36 < fn.rom+fn.size for fn in result) == protected


def test_terminal_alignment_before_float_fields_and_addresses():
    import struct

    words = [0x8C820000, 0x03E00008, 0, 0x8E050074]
    image, functions, segments = fixture(words, cuts=(0, 12, 16))
    image += struct.pack('>4f4I', 1, 50, -50, 0, BASE, BASE+4, BASE+8, BASE+4)
    result = repair(functions, [], image, consoles.get('n64'), segments)
    assert [fn.rom for fn in result] == [0]


@pytest.mark.parametrize('case', ['delay', 'named', 'entry'])
def test_alignment_call_before_a_validated_frame_only_targets_a_delay_slot(case):
    target = BASE+48 if case == 'entry' else BASE+60
    call = 0x0C000000 | ((target >> 2) & 0x3FFFFFF)
    words = [0x8C820000, 0x03E00008, 0, call]+BODY+BODY
    image, functions, segments = fixture(words, cuts=(0, 12, 48, 80))
    symbols = [f'entry = 0x{BASE+12:08X};'] if case == 'named' else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert [fn.rom for fn in result] == [0, 16 if case == 'delay' else 12, 48]


@pytest.mark.parametrize('case', ['data', 'named', 'bad_size'])
def test_address_record_after_closed_assembly_preserves_only_reachable_bodies(case):
    target = BASE+92
    call = 0x0C000000 | ((target >> 2) & 0x3FFFFFF)
    assembly = [0x20840001, 0, 0x03E00008, 0]
    record = [call, 0, 0x03E00008, 0, 44 if case == 'bad_size' else 48, *[BASE+0x400]*7]
    leaf = [0x8C820000, 0x24420001, 0x03E00008, 0]
    words = assembly+record+leaf+BODY
    image, functions, segments = fixture(words, cuts=(0, 80, 112))
    symbols = [f'entry = 0x{BASE+16:08X};'] if case == 'named' else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    expected = [(0, 16), (64, 16), (80, 32)] if case == 'data' else [(0, 80), (80, 32)]
    assert [(fn.rom, fn.size) for fn in result] == expected


def test_folded_call_to_stack_restore_before_an_aligned_frame():
    target = BASE+48+20
    call = 0x0C000000 | ((target >> 2) & 0x3FFFFFF)
    words = [0x03E00008, 0, 0, call]+BODY+BODY
    words[17] = 0x8FBF0014
    image, functions, segments = fixture(words, cuts=(0, 8, 12, 48, 80))
    result = repair(functions, [], image, consoles.get('n64'), segments)
    assert [fn.rom for fn in result] == [0, 16, 48]


@pytest.mark.parametrize('entered', [False, True])
def test_folded_call_after_return_and_before_closed_leaf(entered):
    target = BASE+44
    call = 0x0C000000 | ((target >> 2) & 0x3FFFFFF)
    leaf = [0x8C820000, 0x03E00008, 0]
    words = [*leaf, 0, 0x03E00008, 0, 0xA2030071, call, *leaf, 0x8FB00010, 0x03E00008, 0x27BD0018]
    image, functions, segments = fixture(words, cuts=(0, 32, 44, 56))
    symbols = [f'entry = 0x{BASE+28:08X};'] if entered else ()
    result = repair(functions, [], image, consoles.get('n64'), segments, symbols=symbols)
    assert any(fn.rom <= 28 < fn.rom+fn.size for fn in result) == entered


def test_zero_alignment_can_run_to_end_of_mapping():
    words = [0x8C820000, 0x03E00008, 0, 0, 0, 0]
    image, functions, _ = fixture(words, cuts=(0, 12, 16, 24))
    result = repair(functions[:2], [], image+bytes(16), consoles.get('n64'), [Segment('code', 0, 24, BASE)])
    assert [fn.rom for fn in result] == [0]


@pytest.mark.parametrize('named',[False,True])
def test_called_frame_after_repeated_double_at_the_start_of_a_mapping(named):
    call = 0x0c000000 | ((BASE+32)>>2)&0x3ffffff
    words = [call,0,0x400921fb,0x54442d18,0x400921fb,0x54442d18,0,0,*BODY]
    image,all_functions,_ = fixture(words,cuts=(0,8,20,28,32,64))
    functions = [all_functions[i] for i in (0,2,4)]
    segments = [Segment('entry',0,20,BASE),Segment('code',20,64,BASE+20)]
    symbols = [f'entry_mid = 0x{BASE+20:08X};'] if named else []
    result = repair(functions,[],image,consoles.get('n64'),segments,symbols=symbols)
    assert (20 in {fn.rom for fn in result}) == named


def test_independent_assembly_tail_is_retained_inside_a_closed_leaf():
    from decompnet.rom.split import function_from_text

    words = [0x24820001,0x24a50002,0x00c02021,0,0x00451021,0xac820000,0x03e00008,0]
    image,functions,segments = fixture(words,cuts=(0,16,32))
    console = consoles.get('n64')
    data = []
    result = repair(functions,data,image,console,segments,
                    symbols=[f'{functions[1].name} = 0x{BASE+16:08X};'])
    body, = result
    asm = function_asm(body)
    assert body.size == 32 and console.function_status(body) == 'asm'
    assert function_from_text(body.name,asm,body.segment).entry_aliases == {BASE+16:functions[1].name}
    alias, = data
    assert (alias.name,alias.vram,alias.kind) == (functions[1].name,BASE+16,'func')
    # A separately named entry deeper in the tail requires its own review.
    assert len(repair(functions,[],image,console,segments,symbols=[
        f'tail = 0x{BASE+16:08X};\nthird = 0x{BASE+20:08X};',
    ])) == 2
