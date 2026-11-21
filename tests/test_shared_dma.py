import struct

from decompnet.consoles.n64 import shared_dma
from decompnet.consoles.n64.raw_slots import Slot


def test_shared_dma_reads_constant_calls_and_indexed_slot():
    image = bytearray(0x5000)
    base = 0x80100000
    other_base = 0x80200000
    call = 0x0c000100
    for index in range(3):
        offset = 0x2000 + 0x20*index
        struct.pack_into('>8I', image, index*32, 0x3c0e0000, 0x3c0f0000,
                         0x25ef0000 | (offset+0x20), 0x25ce0000 | offset,
                         0x01c02025, 0x01ee3023, call, 0x3c058010)
        indexed_start = 0x4000 + 0x20*index
        struct.pack_into('>II', image, 0x1000+index*8, indexed_start, indexed_start+0x20)
        for start in (offset, indexed_start):
            struct.pack_into('>III', image, start, 0x27bdffe8, 0x03e00008, 0x27bd0018)
    struct.pack_into('>18I', image, 0x140, 0x3c188000, 0x27181000, 0x000e78c0,
                     0x01f8c821, 0x8f280004, 0x8f290000, *([0]*10), call, 0x3c058020)
    expected = [Slot(off, off+0x20, vram) for start, vram in ((0x2000, base), (0x4000, other_base))
                for off in range(start, start+0x60, 0x20)]
    assert shared_dma.read(image, [(0, 0x400, 0x80000000)], []) == expected

    # An unrelated call must not grant the indexed table a RAM mapping.
    struct.pack_into('>I', image, 0x180, call+1)
    assert shared_dma.read(image, [(0, 0x400, 0x80000000)], []) == expected[:3]


def test_inline_cart_copies_in_recovered_text_establish_distinct_shared_banks():
    image = bytearray(0x4000)
    address = 0x80001400
    loader = 0x0c001200
    entry = 0x0c000000 | ((address>>2)&0x03ffffff)
    expected = []
    for slot in range(4):
        offset = 0x2000+slot*0x40
        struct.pack_into('>9I', image, 0x800+slot*36, 0x3c04b000, 0x24840000 | offset,
                         0x3c058000, 0x3c060000, 0x24c60040, loader, 0x34a51400,
                         entry+4 if slot==3 else entry if slot else 0, 0)
        struct.pack_into('>4I', image, offset, 0x27bdffd8, 0xafbf0024, 0x03e00008, 0x27bd0028)
        struct.pack_into('>II', image, offset+16, 0x03e00008, 0)
        expected.append(Slot(offset, offset+0x40, address))
    resident = [(0x100,0x200,0x80004000)]
    inferred = [(0x800,0xc00,0x80005000)]
    assert shared_dma.read(image,resident,inferred) == expected
    struct.pack_into('>I',image,0x800+3*36+28,0)
    assert shared_dma.read(image,resident,inferred) == []
