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


def test_indirect_loader_call_and_nonreturning_startup():
    from decompnet.consoles.n64.boot_lzss import _indirect_call, startup_sections

    target = 0x8004ba20
    loader = struct.pack('>5I', 0x3c108005, 0x0c012d19, 0x2610ba20, 0x0200f809, 0)
    assert _indirect_call(loader, 0, len(loader), target)
    assert not _indirect_call(loader, 0, len(loader), target + 4)
    text = bytearray(0x80)
    struct.pack_into('>4I', text, 0, 0x3c1d803f, 0x0c000110, 0, 0x0000000d)
    struct.pack_into('>I', text, 0x40, 0x27bdffe0)
    assert startup_sections(text, 0x80000400) == (20, 0x40)


def test_recover_directly_called_loader_past_boot_split(tmp_path):
    import yaml

    from decompnet.consoles.n64.boot_lzss import extend_boot

    path = tmp_path / 'game.yaml'
    path.write_text(yaml.safe_dump({'segments': [
        {'name': 'entry', 'start': 0x1000, 'vram': 0x80000400, 'subsegments': [[0x1000, 'hasm']]},
        {'name': 'main', 'start': 0x1014, 'vram': 0x80000414, 'follows_vram': 'entry',
         'subsegments': [[0x1014, 'c', 'main']]},
        {'type': 'bin', 'start': 0x1040, 'follows_vram': 'main'}, [0x2000]]}))
    image = bytearray(0x2000)
    struct.pack_into('>I', image, 0x1000, 0x0c000114)
    struct.pack_into('>I', image, 0x1050, 0x27bdffe0)
    struct.pack_into('>2I', image, 0x1060, 0x03e00008, 0x27bd0020)
    assert extend_boot(path, image, lambda _: None)
    assert yaml.safe_load(path.read_text())['segments'][2]['start'] == 0x1068
