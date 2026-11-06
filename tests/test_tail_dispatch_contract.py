import pytest

from decompnet.app.wrapper_contracts import tail_dispatch_hint
from decompnet.arch import get
from decompnet.arch.mips_tail_dispatch import read


def code(words=None, big=True):
    return b''.join(w.to_bytes(4,'big' if big else 'little') for w in
                    (words or [0x14c00003,0,0x08000800,0,0x08000c00,0]))


@pytest.mark.parametrize('big', [True,False])
def test_complete_tail_dispatch_preserves_abi_uncertainty(big):
    result=read(code(big=big),big,vram=0x1000)
    assert result['condition_register']=='a2'
    assert result['zero_target']==0x2000 and result['nonzero_target']==0x3000
    assert '64-bit pair' in result['advice'] and 'No signature or profile was changed' in result['advice']
    assert 'original retail compiler' in result['advice']


@pytest.mark.parametrize('index,value', [(0,0x14c00002),(0,0x14400003),(0,0x14c10003),
                                      (1,0x24040000),(2,0x0c000800),(3,1),(4,0x03e00008),
                                      (4,0x08000800),(4,0x08000404),(5,1)])
def test_abstain_on_other_control_flow_or_nonempty_slots(index,value):
    words=[0x14c00003,0,0x08000800,0,0x08000c00,0]
    words[index]=value
    assert read(code(words),vram=0x1000) is None


def test_complete_extent_and_alignment_required():
    assert read(code()[:-4],vram=0x1000) is None
    assert read(code()+b'\0'*4,vram=0x1000) is None
    assert read(code(),vram=0x1001) is None


def test_jump_region_uses_each_instructions_pc():
    result=read(code(),vram=0x8ffffff8)
    assert result['zero_target']==0x90002000 and result['nonzero_target']==0x90003000


def test_brief_connector_without_saved_c_and_unknown_processors():
    fn={'target':code(),'vram':0x1000}
    text=tail_dispatch_hint({'arch':'mips','big_endian':True},fn)
    assert 'sibling-tail-call' in text and 'self tail recursion' in text
    assert tail_dispatch_hint({'arch':'arm','big_endian':True},fn)==''
    assert tail_dispatch_hint({'arch':'mips','big_endian':True},fn|{'target':None})==''
    assert tail_dispatch_hint({'arch':'mips','big_endian':True},fn|{'status':'matched'})==''
    assert tail_dispatch_hint({},fn)==''
    assert get('mips').source_tail_dispatch is read
