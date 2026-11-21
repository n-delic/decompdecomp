"""Only resident loader references can promote parallel arrays to shared overlays."""

import struct

from decompnet.consoles.n64 import indexed_overlays


def test_shared_array_directory_uses_the_declared_slot():
    image = bytearray(0x800)
    loader = 0x40
    vram = 0x80000040
    directory = 0x200
    base = 0x80200000
    count = 3
    starts = (0x400, 0x500, 0x600)
    ends = (0x450, 0x560, 0x660)
    ram_ends = (base+0x50, base+0x70, base+0x60)
    struct.pack_into('>12I', image, directory, *starts, *ends, *([base]*count), *ram_ends)
    for index in range(count):
        address = 0x80000000 + directory + 4*count*index
        struct.pack_into('>2I', image, loader+index*8, 0x3c098000, 0x25290000 | (address & 0xffff))
    for entry in starts:
        struct.pack_into('>I', image, entry, 0x27bdffe0)
    resident = [(loader, 0x100, vram)]
    rows = indexed_overlays.read(image, resident)
    assert [(bank.start, bank.end, bank.vram, bank.ram_end) for bank in rows] == [
        (start, end, base, ram_end) for start, end, ram_end in zip(starts, ends, ram_ends)]
    image[loader+7] += 4
    assert not indexed_overlays.read(image, resident)


def test_constant_dma_and_cache_ranges_bound_resident_banks():
    image = bytearray(0x800)
    base = 0x80070000
    loader = 0x40

    def address(value, target):
        return 0x3c000000 | target<<16 | ((value+0x8000)>>16), 0x24000000 | target<<21 | target<<16 | (value & 0xffff)

    def span(first, last, register, target, saved=False):
        hi_a, lo_a = address(first, 4)
        hi_b, lo_b = address(last, register)
        return [hi_a, hi_b, lo_a, *([0xafbf0014] if saved else []), lo_b, target, 0x00042823 | register<<21]

    for i, start in enumerate((0x400, 0x500)):
        hi_first, lo_first = address(start, 5)
        hi_last, lo_last = address(start+0x100, 24)
        words = span(base, base+0x80, 14, 0x0c000001, saved=True)
        words += span(base+0x80, base+0x100, 15, 0x0c000002)
        words += [hi_first, hi_last, lo_first, lo_last, *address(base, 4), 0x0c000003, 0x03053023]
        struct.pack_into('>21I', image, loader+0x60*i, *words)
    resident = [(loader, 0x180, 0x80000040)]
    banks = indexed_overlays.resident_banks(image, resident, before=0x600)
    assert [(bank.start, bank.text_end, bank.end) for bank in banks] == [(0x400, 0x480, 0x500), (0x500, 0x580, 0x600)]
    image[loader+15*4+3] ^= 1
    assert not indexed_overlays.resident_banks(image, resident, before=0x600)
