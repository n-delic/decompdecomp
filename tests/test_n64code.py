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


def test_discovered_code_preserves_the_original_explicit_bss_extent(tmp_path, monkeypatch):
    import yaml
    from splat.scripts.split import initialize_segments
    from splat.util import options

    from decompnet.consoles.n64 import code
    from decompnet.rom.export import _segments

    # The original main range includes data and code later split into their own segments. Its explicit BSS
    # still starts after all that ROM-backed memory, not after the first newly discovered code segment.
    config = {"name": "synthetic", "options": {"platform": "n64", "basename": "synthetic",
              "base_path": str(tmp_path), "target_path": "rom.z64", "compiler": "GCC"}, "segments": [
        {"name": "main", "type": "code", "start": 0, "vram": 0x80000400, "bss_size": 0x80,
         "subsegments": [[0, "asm"], [0x40, "data"], {"type": "bss", "vram": 0x80001400}]},
        {"type": "bin", "start": 0x1000}, [0x2000]]}
    path = tmp_path / "synthetic.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    (tmp_path / "rom.z64").write_bytes(bytes(0x2000))
    monkeypatch.setattr(code, "unpack_code", lambda _: [])
    monkeypatch.setattr(code, "find_segments", lambda *_, **__: [(0x200, 0x400, 0x80000600)])
    code.add_segments(path, tmp_path, "rom.z64", log=lambda _: None)
    changed = yaml.safe_load(path.read_text())
    options.initialize(changed, [path])
    main = initialize_segments(changed["segments"])[0]
    bss = next(s for s in main.subsegments if s.type == "bss")
    assert (main.rom_end, main.vram_end) == (0x200, 0x80001480)
    assert (bss.vram_start, bss.vram_end) == (0x80001400, 0x80001480)
    exported = _segments(changed)[0]
    assert exported["bss_size"] == 0x80
    assert changed["segments"][0]["bss_size"] == 0xE80  # export does not mutate splat's parent extent
    # Repeating discovery must not grow the BSS a second time.
    code.add_segments(path, tmp_path, "rom.z64", log=lambda _: None)
    assert yaml.safe_load(path.read_text())["segments"][0]["bss_size"] == 0xE80


def test_a_function_longer_than_the_quiet_stretch_joins_its_neighbours():
    from decompnet.consoles.n64.code import close_gaps
    nop = (0).to_bytes(4, "big")
    big = nop * 0x600 + JR_RA.to_bytes(4, "big") + nop  # one 6 KB function: its only return at the end
    rom = bytes(0x1000) + nop * 0x100 + big + nop * 0x100 + bytes(0x100) + nop * 0x40
    a_end = 0x1000 + 0x400
    b_start = a_end + len(big)
    found = [(0x1000, a_end, 0x80001000), (b_start, b_start + 0x400, 0x80001000 + b_start - 0x1000)]
    assert close_gaps(rom, found) == [(0x1000, b_start + 0x400, 0x80001000)]
    # another mapping (an overlay elsewhere), or a gap with no return (data), stays apart
    assert len(close_gaps(rom, [found[0], (b_start, b_start + 0x400, 0x80400000)])) == 2
    quiet = bytes(0x1000) + nop * 0x100 + nop * 0x600 + nop * 0x100
    assert len(close_gaps(quiet, found)) == 2


def test_adjacent_code_mapping_requires_call_evidence_from_both_sides():
    from decompnet.consoles.n64.code import reconcile_adjacent

    base = 0x80300000
    size = 16*32
    following = base+size
    left = b''.join(struct.pack('>8I', *_function(base+i*32, following+i*32)) for i in range(16))
    right = _code(following, 16)
    ranges = [(0, size, base), (size, 2*size, following+8)]
    assert reconcile_adjacent(left+right, ranges) == [(0, 2*size, base)]
    assert reconcile_adjacent(_code(base, 16)+right, ranges) == ranges
    assert reconcile_adjacent(left+_code(following+8, 16), ranges) == ranges


def test_ipl3_dma_proves_the_initial_cartridge_mapping():
    from decompnet.consoles.n64.code import _BOOT_DMA, boot_switch_range
    from decompnet.rom.split import Segment

    image = bytearray(0x101000)
    image[8:12] = (0x80000400).to_bytes(4, 'big')
    image[0x100:0x100+len(_BOOT_DMA)] = _BOOT_DMA
    segments = [Segment('entry', 0x1000, 0x1100, 0x80000400)]
    assert boot_switch_range(image, segments) == [
        dict(rom_start=0x1000, rom_end=0x101000, vram=0x80000400,
             target_prefix=0x80000000, boot_copy=True)]
    image[0x100+len(_BOOT_DMA)-1] ^= 1
    assert not boot_switch_range(image, segments)


def test_discovery_rejects_an_empty_range_at_the_first_plausible_entry(monkeypatch):
    from decompnet.consoles.n64 import code

    rom = bytearray(0x5000)
    rom[0x1000:0x1800] = _code(0x80000400, 64)
    rom[0x3000:0x3100] = _code(0x80002400, 8)
    monkeypatch.setattr(code, 'place', lambda *_: (0x3100, 0x3100, 0x80002500, 8))
    assert find_segments(rom, [(0x1000,0x1800,0x80000400)]) == []
