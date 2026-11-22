"""Boot LZSS decoding requires a complete, bounded stream and a loader call."""

import struct

from decompnet.consoles.n64 import boot_lzss, code


def test_literals_and_overlapping_ring_copy():
    # Three literals followed by a six-byte overlapping copy from the ring.
    stream = bytes([7]) + b'ABC' + bytes([0xee, 0xf3])
    assert boot_lzss.decode(stream, 9) == b'ABCABCABC'
    assert boot_lzss.decode(stream[:-1], 9) is None
    assert boot_lzss.decode(stream + b'X', 9) is None
    assert boot_lzss.decode(stream, 8) is None
    assert boot_lzss.decode(stream, 0) is None


def test_header_requires_a_call_to_mapped_output(monkeypatch):
    program = bytes.fromhex('27bdfff0afbf000c03e0000827bd0010')
    packed = b''.join(b'\xff' + program[i:i + 8] for i in range(0, len(program), 8))
    image = bytearray(0x1100)
    target = 0x80000500
    struct.pack_into('>I', image, 0x1000, 0x0c000000 | (target >> 2 & 0x3ffffff))
    image.extend(struct.pack('>III4sII', 1, 0, 24, b'LZSS', len(program), len(packed)) + packed)
    ranges = [(0x1000, 0x1020, 0x80000400)]
    monkeypatch.setattr(code, '_looks_like_code', lambda data: data == program)
    assert boot_lzss.find(image, ranges) == ((target, program, b''),)
    image[0x1000:0x1004] = bytes(4)
    assert boot_lzss.find(image, ranges) is None


def test_boot_trap_before_a_helper_stays_in_startup(tmp_path):
    import yaml

    from decompnet.consoles.n64.boot_mapping import extend_startup_trap

    path = tmp_path / 'game.yaml'
    path.write_text(yaml.safe_dump({'segments': [
        {'name': 'entry', 'start': 0x1000, 'vram': 0x80000400, 'subsegments': [[0x1000, 'hasm']]},
        {'name': 'main', 'start': 0x1010, 'vram': 0x80000410, 'follows_vram': 'entry',
         'subsegments': [[0x1010, 'c', 'main']]}, [0x1100]]}))
    image = bytearray(0x1100)
    struct.pack_into('>4I', image, 0x1008, 0x0c000110, 0, 0x0000000d, 0x3c088000)
    assert extend_startup_trap(path, image, lambda _: None)
    main = yaml.safe_load(path.read_text())['segments'][1]
    assert main['start'] == 0x1014
    assert main['subsegments'] == [[0x1014, 'c', 'main']]
