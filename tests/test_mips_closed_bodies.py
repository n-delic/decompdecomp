"""Compiler-scheduled epilogues and large bodies still need closed, balanced flow."""
import pytest

from decompnet import consoles
from decompnet.arch.mips_code_recovery import called_extent, leaf_extent

BASE=0x80001000
FRAME=0x27bdffe8
POP=0x27bd0018
RETURN=0x03e00008


def image(words,big=True):return b''.join(w.to_bytes(4,'big' if big else 'little') for w in words)


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_compiler_can_pop_immediately_before_return(platform):
    console=consoles.get(platform)
    words=[FRAME,0xafbf0014,0x8fbf0014,POP,RETURN,0]
    data=image(words,console.big_endian)
    assert called_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant)==len(data)


@pytest.mark.parametrize('words',[
    [FRAME,0xafbf0014,0x10800003,0,0x8fbf0014,POP,RETURN,0],  # branch bypasses pop
    [FRAME,0xafbf0014,0x10000001,POP,RETURN,0],  # pop is another transfer's delay slot
    [FRAME,0xafbf0014,0x8fbf0014,POP,RETURN,POP],  # double pop
    [FRAME,0xafbf0014,0x8fbf0014,0x27bd0010,RETURN,0],  # wrong amount
    [FRAME,0xafbf0014,0x8fbf0014,POP,RETURN,0x10000000],  # transfer in JR delay slot
])
def test_pre_return_pop_rejects_bypasses_delay_slot_execution_and_double_pops(words):
    data=image(words)
    assert called_extent(data,0,len(data),BASE) is None


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_directly_called_empty_and_constant_return_stubs(platform):
    console=consoles.get(platform)
    for words in ([RETURN,0],[0x24020001,RETURN,0]):
        data=image(words,console.big_endian)
        assert leaf_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant)==len(data)


def test_large_bodies_use_their_bounded_byte_budget_instead_of_a_smaller_audit_limit():
    words=[FRAME,0xafbf0014]+[0]*5000+[0x8fbf0014,POP,RETURN,0]
    data=image(words)
    assert called_extent(data,0,len(data),BASE)==len(data)
    data=image([0x24020001]+[0]*1500+[RETURN,0])
    assert leaf_extent(data,0,len(data),BASE)==len(data)
    # A return beyond the 64 KiB maximum still cannot close a body.
    data=image([FRAME,0xafbf0014]+[0]*16384+[0x8fbf0014,POP,RETURN,0])
    assert called_extent(data,0,len(data),BASE) is None


def test_branch_to_the_pre_return_pop_executes_the_full_epilogue():
    words=[FRAME,0xafbf0014,0x10800002,0,0x8fbf0014,POP,RETURN,0]
    data=image(words)
    assert called_extent(data,0,len(data),BASE)==len(data)


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
@pytest.mark.parametrize('move',[0x00601021,0x00001025])  # v0 = v1 or zero
def test_return_value_can_be_scheduled_after_stack_restore(platform, move):
    console=consoles.get(platform)
    data=image([FRAME,0xafbf0014,0x24030001,0x8fbf0014,POP,move,RETURN,0],console.big_endian)
    assert called_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant)==len(data)


@pytest.mark.parametrize('words',[
    [FRAME,0xafbf0014,0x10800003,0,POP,0x00601021,RETURN,0],  # skip restore
    [FRAME,0xafbf0014,0x10000001,POP,0x00601021,RETURN,0],  # restore in a delay slot
    [FRAME,0xafbf0014,0x8fbf0014,POP,0x00601021,RETURN,POP],  # double restore
    [FRAME,0xafbf0014,0x8fbf0014,POP,0x03a01021,RETURN,0],  # read restored sp
    [FRAME,0xafbf0014,0x8fbf0014,POP,0x8fa20000,RETURN,0],  # load after restore
])
def test_separated_stack_restore_rejects_unsafe_epilogues(words):
    data=image(words[:1]+[0x24030001]+words[1:])
    assert called_extent(data,0,len(data),BASE) is None


def test_a_leaf_cannot_have_a_transfer_inside_its_return_delay_slot():
    data=image([0x24020001,RETURN,0x10000000])
    assert leaf_extent(data,0,len(data),BASE) is None


LUI_V0=0x3c028010  # lui $v0, 0x8010
LBU_V0=0x904202a0  # lbu $v0, 0x2a0($v0)


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_loads_scheduled_ahead_of_the_frame_stay_in_the_body(platform):
    """An N64 game's GCC put `lui $v0; lbu $v0` before `addiu $sp`: the body still opens at the first load."""
    console=consoles.get(platform)
    words=[LUI_V0,LBU_V0,FRAME,0xafbf0014,0x8fbf0014,POP,RETURN,0]
    data=image(words,console.big_endian)
    assert called_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant)==len(data)


@pytest.mark.parametrize('lead',[
    [0xafa40000],  # a store
    [0x10000001,0],  # a transfer
    [0x00808021],  # writes a saved register ($s0)
    [0x8fa20010],  # reads $sp
    [LUI_V0]*6,  # more than the compiler schedules
])
def test_only_scratch_loads_and_arithmetic_may_come_before_the_frame(lead):
    data=image(lead+[FRAME,0xafbf0014,0x8fbf0014,POP,RETURN,0])
    assert called_extent(data,0,len(data),BASE) is None


def test_a_loop_back_into_scheduled_loads_is_no_entry():
    words=[LUI_V0,FRAME,0x1000fffd,0,0x8fbf0014,POP,RETURN,0]  # the branch at 8 goes back to the lui
    assert called_extent(image(words),0,32,BASE) is None


@pytest.mark.parametrize('last_store', [0xafa7000c, 0xafa70010])
def test_only_the_complete_o32_argument_home_sequence_can_precede_the_frame(last_store):
    words = [0xafa50004, 0xafa60008, last_store, FRAME, 0xafbf0014, 0x8fbf0014, POP, RETURN, 0]
    data = image(words)
    expected = len(data) if last_store == 0xafa7000c else None
    assert called_extent(data, 0, len(data), BASE) == expected


def test_cache_maintenance_can_have_a_closed_frameless_extent():
    from decompnet.arch.mips_code_recovery import leaf_extent

    data = image([0xbd150000, 0x25080010, RETURN, 0])
    assert leaf_extent(data, 0, len(data), BASE) == len(data)


@pytest.mark.parametrize('damage', [None, 'no_save', 'wrong_restore', 'clobber_fp', 'enter_setup', 'enter_epilogue'])
def test_frame_pointer_body_requires_preserved_frame_and_closed_entry(damage):
    words = [0x27bdffe0, 0xafbf001c, 0xafbe0018, 0x03a0f021,
             0x24840001, 0x03c0e821, 0x8fbf001c, 0x8fbe0018, 0x03e00008, 0x27bd0020]
    if damage == 'no_save': words[2] = 0
    if damage == 'wrong_restore': words[7] = 0x8fbe0014
    if damage == 'clobber_fp': words[4] = 0x241e0001
    if damage == 'enter_setup': words[4] = 0x1000fffd
    if damage == 'enter_epilogue': words[4] = 0x10000001
    data = image(words)
    assert called_extent(data, 0, len(data), BASE) == (len(data) if damage is None else None)


@pytest.mark.parametrize('damage', [None, 'enter_pop', 'wrong_restore', 'delay_pop_twice'])
def test_frame_pointer_body_can_pop_before_return(damage):
    words = [0x27bdffe0, 0xafbf001c, 0xafbe0018, 0x03a0f021,
             0x24840001, 0x03c0e821, 0x8fbf001c, 0x8fbe0018, 0x27bd0020, 0x03e00008, 0]
    if damage == 'enter_pop': words[4] = 0x10000003
    if damage == 'wrong_restore': words[7] = 0x8fbe0014
    if damage == 'delay_pop_twice': words[-1] = 0x27bd0020
    data = image(words)
    assert called_extent(data, 0, len(data), BASE) == (len(data) if damage is None else None)


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_large_stack_frame_requires_a_matching_return(platform):
    console=consoles.get(platform)
    words=[0x27bddfe8,0xafbf2014,0x24020001,0x8fbf2014,RETURN,0x27bd2018]
    data=image(words,console.big_endian)
    assert called_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant)==len(data)
    words[-1]=0x27bd2010
    data=image(words,console.big_endian)
    assert called_extent(data,0,len(data),BASE,big_endian=console.big_endian,cpu=console.cpu_variant) is None
