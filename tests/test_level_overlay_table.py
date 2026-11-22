"""A level loader's directory gives each mutually exclusive overlay its own scope."""
import struct

import pytest

from decompnet.consoles.n64.level_overlay_table import companion, read


def image():
    data = bytearray(0x8000)
    loader = [0x3c118000, 0x02338821, 0x8e310c00,
              0x3c028000, 0x00531021, 0x8c420c04,
              0x00519023, 0x12400008, 0, 0x3c108011, 0x26103910]
    struct.pack_into(f'>{len(loader)}I', data, 0x1100, *loader)
    for i in range(4):
        lo = 0x3000 + i*0x1000
        struct.pack_into('>4I', data, 0x1800+i*0x54, lo, lo+0x1000, 0x80114920, 0x80113910)
        struct.pack_into('>I', data, lo+0x10, 0x03e00008)
    return data


@pytest.mark.parametrize('damage', [None, 'loader', 'gap', 'entry', 'no_return'])
def test_directory_requires_indexed_loader_and_complete_bounds(damage):
    data = image()
    if damage == 'loader': data[0x110c] = 0
    if damage == 'gap': struct.pack_into('>I', data, 0x1800+0x54, 0x4100)
    if damage == 'entry': struct.pack_into('>I', data, 0x1800+0x54+12, 0x80000000)
    if damage == 'no_return': struct.pack_into('>I', data, 0x3010, 0)
    slots = read(data, [(0x1000, 0x2000, 0x80000400)])
    assert len(slots) == (4 if damage is None else 0)
    if slots:
        assert {slot.vram for slot in slots} == {0x80113910}


@pytest.mark.parametrize('damage', [None, 'not_adjacent', 'no_call', 'no_return'])
def test_adjacent_companion_requires_explicit_copy(damage):
    data = image()
    copy = [0x3c020000, 0x24423000, 0x3c040000, 0x24842000, 0x00443023,
            0x10c00004, 0xafbf0010, 0x3c05801e, 0x0c000100, 0x24a5a800]
    struct.pack_into(f'>{len(copy)}I', data, 0x1180, *copy)
    struct.pack_into('>I', data, 0x2010, 0x03e00008)
    if damage == 'not_adjacent': struct.pack_into('>I', data, 0x1184, 0x24422ff0)
    if damage == 'no_call': struct.pack_into('>I', data, 0x11a0, 0)
    if damage == 'no_return': struct.pack_into('>I', data, 0x2010, 0)
    taken = [(0x1000, 0x2000, 0x80000400)]
    result = companion(data, taken, read(data, taken))
    assert (result is not None) == (damage is None)
    if result:
        assert (result.start, result.end, result.vram) == (0x2000, 0x3000, 0x801da800)


def test_reused_banks_in_32_byte_level_records_are_deduplicated():
    data = image()
    data[0x1800:0x1a00] = bytes(0x200)
    struct.pack_into('>I', data, 0x10f0, 0x00049940)  # sll s3, a0, 5
    for i, bank in enumerate((0, 1, 2, 1, 3, 0)):
        lo = 0x3000+bank*0x1000
        struct.pack_into('>3I', data, 0x1800+i*0x20, lo, lo+0x1000, 0x80114920)
    copy = [0x3c020001, 0x24428000, 0x3c040000, 0x24847000, 0x00443023,
            0x10c00004, 0xafbf0010, 0x3c05801e, 0x0c000100, 0x24a5a800]
    struct.pack_into(f'>{len(copy)}I', data, 0x1180, *copy)
    struct.pack_into('>I', data, 0x7010, 0x03e00008)
    taken = [(0x1000, 0x2000, 0x80000400)]
    slots = read(data, taken)
    assert len(slots) == 4
    helper = companion(data, taken, slots)
    assert helper and (helper.start,helper.end) == (0x7000,0x8000)
