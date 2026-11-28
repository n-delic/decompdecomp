"""Shared file banks need both a runtime witness and loader call evidence."""
import struct

from decompnet.consoles.n64 import shared_file_banks


def sample():
    image = bytearray(0x2000)
    base = 0x80200000
    starts = [0x1000, 0x1100, 0x1200, 0x1300]
    for index, bank in enumerate((2, 0, 3, 1)):
        struct.pack_into('>3I', image, 0x300+index*12, starts[bank], starts[bank]+0x100, 0)
        # load(index, shared_address), with the destination copied in the delay slot
        struct.pack_into('>5I', image, 0x100+index*20, 0x24040000 | index, 0x3c118020,
                         0x26310000, 0x0c000000 | ((0x80000500 >> 2) & 0x3ffffff), 0x02202821)
    # Indexed 12-byte records; the third field distinguishes the raw files.
    struct.pack_into('>17I', image, 0x200, 0x00041040, 0x00441021, 0x00022880,
                     0x3c038000, 0x00651821, 0x8c630600, 0x3c038000, 0x00651821,
                     0x8c630604, 0x3c038000, 0x00651821, 0x8c630608, 0x00a03821,
                     0x00e02821, 0x0c000200, 0x00c43023, 0x03e00008)
    for start in starts:
        for offset in (0, 0x20, 0x40, 0x60):
            struct.pack_into('>4I', image, start+offset, 0x27bdffe0,
                             0x0c000000 | (((base+offset) >> 2) & 0x3ffffff),
                             0x03e00008, 0x27bd0020)
    return image, [(0x100, 0x300, 0x80000400)], [(0x1200, 0x1280, base)]


def test_directory_recovers_only_runtime_anchored_shared_banks():
    image, taken, observed = sample()
    slots, ranges = shared_file_banks.read(image, taken, observed)
    assert [slot.start for slot in slots] == [0x1000, 0x1100, 0x1200, 0x1300]
    assert all(slot.vram == observed[0][2] for slot in slots)
    assert all(end == start+0x70 for start, end, _ in ranges)
    assert shared_file_banks.read(image, taken, []) == ([], [])
    changed = bytearray(image)
    struct.pack_into('>I', changed, 0x300+8, 1)
    assert shared_file_banks.read(changed, taken, observed) == ([], [])


def test_every_bank_requires_a_loader_call_to_the_shared_address():
    image, taken, observed = sample()
    struct.pack_into('>I', image, 0x100+20+4, 0x3c118021)
    assert shared_file_banks.read(image, taken, observed) == ([], [])


def test_directory_without_matching_loader_is_not_promoted():
    image, taken, observed = sample()
    struct.pack_into('>I', image, 0x200+11*4, 0x8c63060c)
    assert shared_file_banks.read(image, taken, observed) == ([], [])


def test_directory_reader_without_raw_transfer_is_not_a_loader():
    image, taken, observed = sample()
    struct.pack_into('>I', image, 0x200+15*4, 0)
    assert shared_file_banks.read(image, taken, observed) == ([], [])
