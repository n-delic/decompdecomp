import struct

from decompnet.consoles.n64.runtime_overlays import regions


def fixture():
    rom = bytearray(b'\xff' * 0x7000)
    ram = bytearray(0x110000)
    # The active bank maps ROM 0x1000 to RAM 0x80100000. A different
    # recorded bank occupies the three called RAM addresses.
    for index, offset in enumerate((0x1100, 0x1200, 0x1300)):
        struct.pack_into('>5I', rom, offset, 0x27bdfff0, 0xafbf000c, 0x24020000 | index,
                         0x03e00008, 0x27bd0010)
    for n, target in enumerate((0x80100100, 0x80100200, 0x80100300)):
        struct.pack_into('>4I', rom, 0x2800+n*16, 0x0c000000 | (target >> 2 & 0x3ffffff), 0, 0, 0)
    ram[0x100000:0x103000] = rom[0x1000:0x4000]
    known = [(0x2800, 0x3800, 0x80101800), (0x5000, 0x6000, 0x80100100)]
    return rom, ram, known


def test_observed_overlapping_bank_requires_multiple_correlated_calls():
    rom, ram, known = fixture()
    assert regions(rom, [ram], known) == [(0x1100, 0x1314, 0x80100100)]
    # No reference can be borrowed from a different captured caller.
    ram[0x101820:0x101830] = bytes(16)
    assert regions(rom, [ram], known) == []


def test_existing_rom_mapping_and_unchanged_runtime_are_not_reassigned():
    rom, ram, known = fixture()
    moved = set()
    assert not regions(rom, [ram], known + [(0x1100, 0x1400, 0x80200100)], moved=moved)
    assert moved and all(lo <= 0x1100 < 0x1314 <= hi and base-lo == 0x800ff000
                         for lo, hi, base in moved)
    # Fewer than three independently observed targets are not mapping evidence.
    ram[0x101820:0x101830] = bytes(16)
    moved.clear()
    assert not regions(rom, [ram], known + [(0x1100, 0x1400, 0x80200100)], moved=moved)
    assert not moved
    ram[0x101820:0x101830] = rom[0x2820:0x2830]
    for offset in (0x100, 0x200, 0x300):
        ram[0x100000+offset:0x100000+offset+32] = rom[0x5000+offset-0x100:0x5000+offset-0x100+32]
    moved.clear()
    assert not regions(rom, [ram], known, moved=moved)
    assert not moved


def test_longer_observation_requires_many_cross_bank_interior_calls():
    from decompnet.consoles.n64.runtime_overlays import ambiguous

    rom = bytearray(0x6000)
    for index in range(8):
        at = 0x1000 + index*0x40
        struct.pack_into('>6I', rom, at, 0x27bdfff0, 0xafbf000c, 0x24020001,
                         0x8fbf000c, 0x03e00008, 0x27bd0010)
        target = 0x80100000+index*0x40+8
        struct.pack_into('>I', rom, 0x4000+index*4, 0x0c000000 | (target >> 2 & 0x3ffffff))
    known = [(0x1000, 0x1800, 0x80100000), (0x4000, 0x4100, 0x80200000)]
    assert ambiguous(rom, known)
    struct.pack_into('>I', rom, 0x401c, 0)
    assert not ambiguous(rom, known)


def test_resident_callers_can_prove_an_overlapping_runtime_bank():
    rom, ram, known = fixture()
    # Callers live in another resident mapping, not in the newly observed bank.
    ram.extend(bytes(0x201000-len(ram)))
    callers = bytes(rom[0x2800:0x2830])
    rom[0x4000:0x4030] = callers
    ram[0x200000:0x200030] = callers
    rom[0x2800:0x2830] = bytes(0x30)
    ram[0x101800:0x101830] = bytes(0x30)
    known = [(0x4000, 0x4100, 0x80200000), (0x5000, 0x6000, 0x80100100)]
    assert regions(rom, [ram], known) == [(0x1100, 0x1314, 0x80100100)]
    # Missing a verified caller leaves fewer than three independent targets.
    ram[0x200020:0x200030] = bytes(16)
    assert not regions(rom, [ram], known)
