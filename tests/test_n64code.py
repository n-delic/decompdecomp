"""N64 code outside the main segment (consoles/n64/code.py): found, and placed at the address it runs from, from its own
calls. Super Mario 64's engine (0x80378800) and goddard (0x8016F000) segments are found this way; here a synthetic
ROM stands in for a game."""

import struct

from decompnet.consoles.n64.code import find_segments

NOP, JR_RA = 0, 0x03E00008


def _function(vram: int, callee: int) -> list[int]:
    """addiu sp,-0x18; sw ra; jal callee; nop; lw ra; addiu sp,0x18; jr ra; nop"""
    return [0x27BDFFE8, 0xAFBF0014, 0x0C000000 | ((callee >> 2) & 0x03FFFFFF), NOP, 0x8FBF0014, 0x27BD0018, JR_RA, NOP]


def _code(vram: int, count: int, calls_into: int | None = None) -> bytes:
    words = []
    for i in range(count):
        here = vram + i * 32
        callee = vram + ((i + 1) % count) * 32 if calls_into is None or i % 2 else calls_into + (i % 7) * 32
        words += _function(here, callee)
    return struct.pack(f">{len(words)}I", *words)


def test_a_segment_loaded_later_is_found_at_its_address():
    rom = bytearray(0x200000)
    main = _code(0x80000400, 64, calls_into=0x80300000)  # the main code calls into the other segment
    rom[0x1000:0x1000 + len(main)] = main
    extra = _code(0x80300000, 120)
    rom[0x100000:0x100000 + len(extra)] = extra
    rom[0x180000:0x180400] = bytes(range(256)) * 4  # data: no code in it
    found = find_segments(bytes(rom), [(0x1000, 0x1000 + len(main), 0x80000400)])
    assert found == [(0x100000, 0x100000 + len(extra), 0x80300000)]


def test_a_segment_starts_where_its_code_does_not_at_its_first_frame():
    """On an N64 game the code after the main segment began with two functions that make no stack frame, and the
    third loaded a value before making its frame: found at that frame, the segment lost two functions and the first
    two instructions of the third, which could then never match. It starts where the main segment's data ends."""
    rom = bytearray(0x200000)
    main = _code(0x80000400, 64, calls_into=0x80300040)
    rom[0x1000:0x1000 + len(main)] = main
    data_end = 0xFFFF0  # the main segment's data, then this segment
    rom[0xFFF00:data_end] = bytes(range(0xE0)) + bytes(0x10)  # data, then padding
    leaves = [0x3C0E8010, 0x95CE0004, JR_RA, 0xA42E0008,  # lui; lhu; jr ra; sh: no frame
              0x3C048010, JR_RA, 0x94840006]  # lui; jr ra; lhu
    early = [0x3C02800E, 0x8C420010]  # lui v0; lw v0: before the frame of the function after them
    head = struct.pack(f">{len(leaves) + len(early)}I", *leaves, *early)
    extra = _code(0x80300040, 120)  # (its first function's frame, right after `early`)
    start = data_end
    rom[start:start + len(head)] = head
    rom[start + len(head):start + len(head) + len(extra)] = extra
    vram = 0x80300040 - len(head)
    found = find_segments(bytes(rom), [(0x1000, 0x1000 + len(main), 0x80000400)], ends=(data_end,))
    assert [(s, hex(v)) for s, _, v in found] == [(start, hex(vram))]


def test_code_at_an_address_already_taken_is_left_out():
    rom = bytearray(0x200000)
    main = _code(0x80300000, 64)
    rom[0x1000:0x1000 + len(main)] = main
    overlay = _code(0x80300000, 120)  # an overlay loaded where other code runs: addresses would clash
    rom[0x100000:0x100000 + len(overlay)] = overlay
    assert find_segments(bytes(rom), [(0x1000, 0x1000 + len(main), 0x80300000)]) == []


def test_nothing_but_data_finds_nothing():
    rom = bytes(range(256)) * 0x2000
    assert find_segments(rom, [(0x1000, 0x2000, 0x80000400)]) == []


def test_packed_code_at_a_tlb_address_is_unpacked_and_placed():
    """Perfect Dark keeps its code packed (0x1173, the unpacked size, a deflate stream) and runs it at 0x7F000000
    through the TLB: unpacked, appended, and placed at its address."""
    import zlib

    from decompnet.consoles.n64.code import unpack_code

    code = _code(0x7F000000, 160)  # its calls are 0x7F... addresses: a jal keeps the top four bits
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = b"\x11\x73" + len(code).to_bytes(3, "big") + packer.compress(code) + packer.flush()
    rom = bytearray(0x100000)
    main = _code(0x80000400, 40)
    rom[0x1000:0x1000 + len(main)] = main
    rom[0x40000:0x40000 + len(packed)] = packed
    blocks = unpack_code(bytes(rom))
    assert blocks == [(0x40000, code)]
    image = bytes(rom) + bytes(0x2000) + code
    found = find_segments(image, [(0x1000, 0x1000 + len(main), 0x80000400)])
    assert (len(rom) + 0x2000, len(image), 0x7F000000) in found


def test_yaz0():
    from decompnet.consoles.n64.code import yaz0

    # "abcabcabc": three literals, then a back-reference 3 back, 6 long (0x40 >> 4 = 4, + 2)
    data = b"Yaz0" + (9).to_bytes(4, "big") + bytes(8) + bytes([0b11100000]) + b"abc" + bytes([0x40, 0x02])
    assert yaz0(data) == b"abcabcabc"
