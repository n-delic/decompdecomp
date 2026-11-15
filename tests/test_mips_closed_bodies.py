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


def test_a_leaf_cannot_have_a_transfer_inside_its_return_delay_slot():
    data=image([0x24020001,RETURN,0x10000000])
    assert leaf_extent(data,0,len(data),BASE) is None
