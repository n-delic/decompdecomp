import hashlib
import struct

from decompnet.consoles.n64 import loader_code, named_dma, raw_slots


def test_named_dma_requires_the_loader_and_directory(monkeypatch):
    image = bytearray(0x400)
    for index in range(9):
        struct.pack_into('>4I', image, 0x104+16*index, 0x80000500+index*4, 0,
                         0x200+index*0x10, 0x210+index*0x10)
    image[0x20:0x24] = b'load'
    for key, start, end in (('_DIRECTORY', 0x100, 0x194), ('_LOADER', 0x20, 0x24)):
        monkeypatch.setattr(named_dma, key, (start, end-start, hashlib.sha256(image[start:end]).hexdigest()))
    resident = [(0, 0x100, 0x80000400)]
    assert named_dma.read(image, resident) == [
        raw_slots.Slot(0x200+i*0x10, 0x210+i*0x10, 0x80170000 if i < 8 else 0x80178df0)
        for i in range(1, 9)
    ]
    assert named_dma.read(image, []) == []
    image[0x110] ^= 1
    assert named_dma.read(image, resident) == []


def test_raw_slot_supplies_a_declared_switch_table_mapping(monkeypatch):
    slot = raw_slots.Slot(0x400, 0x800, 0x80100000)

    def recover(image, segments, banks, resident, log, *, table_ranges, entries):
        assert table_ranges == [{'rom_start': slot.start, 'rom_end': slot.end, 'vram': slot.vram}]
        assert entries == (slot.vram,)
        return []

    monkeypatch.setattr(loader_code, 'complete', recover)
    assert raw_slots.recover_registered(b'', [slot], [], print) == []
