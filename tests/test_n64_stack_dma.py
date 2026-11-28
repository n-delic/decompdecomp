"""Only a bounded PI message and an independently mapped overlay prove a data range."""
import struct

import pytest

from decompnet.consoles.n64 import stack_dma
from decompnet.rom.split import Segment


def fixture():
    image = bytearray(0x800)
    caller = [0x3c118001,0x26310000,0x00002821,0x00a03021,0x3c07b000,0x24e70400,0x3c02b000,0x24420800,
              0x00471023,0x3c108002,0x26101000,0xafb10010,0xafa20014,0x0c000480,0xafb00018]
    callee = [0x27bdffe8,0xafbf0014,0x8fa40028,0x8fa8002c,0x8fa30030,0xae030004,0xae040008,
              0xae07000c,0xae080010,0x0c000500,0,0x8fbf0014,0x03e00008,0x27bd0018]
    struct.pack_into(f'>{len(caller)}I',image,0,*caller)
    struct.pack_into(f'>{len(callee)}I',image,0x200,*callee)
    segments = [Segment('resident',0,0x300,0x80001000),Segment('overlay',0x500,0x600,0x80010100)]
    return image,segments


def test_constant_pi_read_proves_scoped_table_mapping():
    image, segments = fixture()
    assert stack_dma.ranges(image,segments) == [dict(rom_start=0x400,rom_end=0x800,vram=0x80010000,
                                                      target_prefix=0x80000000,code_delta=0x8000fc00,
                                                      scoped_copy=True)]


@pytest.mark.parametrize('offset,word',[(0,0), (0x24,0x26310001), (0x30,0), (0x38,0), (0x1c,0x3c020001), (0x200+4*7,0)])
def test_missing_mapping_or_pi_evidence_is_rejected(offset,word):
    image, segments = fixture()
    struct.pack_into('>I',image,offset,word)
    assert stack_dma.ranges(image,segments) == []


def test_transfer_requires_read_mode_and_matching_code_mapping():
    image, segments = fixture()
    struct.pack_into('>I',image,8,0x24050001)  # a1 is no longer zero, so the read direction is unproven
    assert not stack_dma.ranges(image,segments)
    image, segments = fixture()
    segments[1] = Segment('different mapping',0x500,0x600,0x80010200)
    assert not stack_dma.ranges(image,segments)
