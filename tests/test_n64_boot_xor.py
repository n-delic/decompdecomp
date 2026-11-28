"""A boot-time in-place transform must stay tied to its original cartridge bytes."""

import struct

from decompnet.consoles.n64 import boot_gzip, boot_xor


def _example():
    start = 0x80002400
    # Independent framed functions give the decoded output recognisable code.
    body = bytes.fromhex('27bdfff0 03e00008 27bd0010') * (0x1000 // 12)
    body += bytes(0x1000 - len(body))
    encoded = bytearray(len(body))
    encoded[0] = body[0]
    for at in range(1, len(body)):
        encoded[at] = body[at] ^ encoded[at - 1]
    rom = bytearray(0x4000)
    # The loop decodes [start, end] backwards, leaving its first byte intact.
    loop = (0x3c108000, 0x261033ff, 0x3c118000, 0x26312400,
            0x12300008, 0, 0x92020000, 0x9203ffff, 0x00431026,
            0xa2020000, 0x2610ffff, 0x1630fffa, 0)
    struct.pack_into('>13I', rom, 0x1040, *loop)
    rom[0x3000:0x4000] = encoded
    return bytes(rom), body, [(0x1000, 0x1200, 0x80000400)], start


def test_boot_xor_requires_the_complete_loader_loop_and_bounded_source():
    rom, body, ranges, start = _example()
    program, = boot_xor.find(rom, ranges)
    assert program[0] == start
    assert program[1] + program[2] == body
    changed = bytearray(rom); changed[0x1064] ^= 1  # Change the loop's store.
    assert boot_xor.find(bytes(changed), ranges) is None
    assert boot_xor.find(rom[:0x3fff], ranges) is None
    assert boot_xor.find(rom, ranges + [(0x3000, 0x4000, start)]) is None


def test_boot_xor_export_verifies_the_original_bytes():
    rom, body, ranges, _ = _example()
    expanded = rom + body
    assert boot_gzip.verify_analysis(rom, expanded, ranges)
    altered = bytearray(expanded); altered[-20] ^= 1
    assert not boot_gzip.verify_analysis(rom, bytes(altered), ranges)
    altered = bytearray(rom); altered[0x3010] ^= 1
    assert not boot_gzip.verify_analysis(bytes(altered), expanded, ranges)
