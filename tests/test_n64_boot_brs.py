"""Bounded decoding for the boot loader's chunked LZ stream."""

import struct

import pytest

from decompnet.consoles.n64.boot_brs import _decode, unpack


def test_literals_and_end_marker():
    stream = b'\x0cAB\x00\x00'
    assert _decode(stream, 2) == b'AB'
    assert _decode(stream + b'\xff\xff', 2) == b'AB'
    assert _decode(stream, 1) is None
    assert _decode(stream[:-1], 2) is None
    assert _decode(stream + b'\x01', 2) is None


def test_chunk_header_requires_all_bounded_streams():
    stream = b'\x0cAB\x00\x00'
    header = b'BRSOHYES' + struct.pack('>8I', *(2, len(stream)) * 4)
    image = header + stream * 4
    assert unpack(image, 0) == b'AB' * 4
    assert unpack(image[:-1], 0) is None
    assert unpack(b'X' + image, 0) is None


def _literal_stream(data):
    assert len(data) % 8 == 0
    return b''.join(b'\x00' + data[i:i + 8] for i in range(0, len(data), 8)) + b'\x03\x00\x00'


def test_replacement_image_keeps_the_two_original_entry_words(tmp_path, monkeypatch):
    import yaml

    from decompnet.consoles.n64 import boot_brs, boot_gzip, code

    boot_entry = bytes.fromhex('3c08beef3508dead3c1d803f37bdfef0')
    executable = bytes.fromhex('3c08dead3508beef3c1d803f37bdfef0') + bytes(8) + bytes.fromhex('03e0000800000000')
    blocks = (executable, b'A' * 8, b'B' * 8, b'C' * 8)
    streams = [_literal_stream(block) for block in blocks]
    sizes = [number for block, stream in zip(blocks, streams) for number in (len(block), len(stream))]
    image = bytearray(0x1100)
    image[0x1000:0x1010] = boot_entry
    image.extend(b'BRSOHYES' + struct.pack('>8I', *sizes) + b''.join(streams))
    image = bytes(image)
    boot = [(0x1000, 0x1010, 0x80000400)]
    monkeypatch.setattr(code, '_looks_like_code', lambda data: data.startswith(executable[:8]))
    monkeypatch.setattr(boot_gzip, 'read', lambda *_: {'events': []})
    programs = boot_brs.find(image, boot)
    assert programs == ((0x80000400, executable, b''.join(blocks[1:])),)
    assert boot_brs.find(image, [(0x1000, 0x1010, 0x80001000)]) is not None
    changed = bytearray(image)
    changed[0x1008] ^= 1
    assert boot_brs.find(bytes(changed), boot) is None

    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump({'options': {}, 'segments': [
        {'type': 'code', 'name': 'entry', 'start': 0x1000, 'vram': 0x80000400,
         'subsegments': [[0x1000, 'c', 'entry']]}, [len(image)]]}))
    filename = boot_gzip.add(config, tmp_path, 'game.z64', image, boot, lambda _: None)
    assert (tmp_path / filename).read_bytes() == image + b''.join(blocks)
    segments = yaml.safe_load(config.read_text())['segments']
    assert segments[-3]['type'] == 'bin' and segments[-3]['start'] == len(image)
    assert segments[-2]['start'] == len(image) + 8
    assert segments[-2]['vram'] == 0x80000408
    assert segments[0]['exclusive_ram_id'] == segments[-2]['exclusive_ram_id']
    assert boot_gzip.verify_analysis(image, image + b''.join(blocks), boot)


@pytest.mark.parametrize('decoder', ['brs', 'xor'])
def test_boot_ranges_are_recovered_before_the_replacement(tmp_path, monkeypatch, decoder):
    from decompnet.consoles.n64 import boot_brs, boot_gzip, boot_xor, code, overlays, resident_deflate
    from decompnet.rom import split

    (tmp_path / 'game.z64').write_bytes(bytes(0x2000))
    config = tmp_path / 'game.yaml'
    config.write_text('segments: []\n')
    initial = [(0x1000, 0x1044, 0x80000400)]
    recovered = initial + [(0x2000, 0x3000, 0x80001400)]
    calls = iter((initial, recovered))
    monkeypatch.setattr(split, 'code_ranges', lambda _: next(calls))
    monkeypatch.setattr(overlays, 'add', lambda *args: None)
    monkeypatch.setattr(resident_deflate, 'add', lambda *args: None)
    monkeypatch.setattr(boot_brs, 'find', lambda *args: decoder == 'brs')
    monkeypatch.setattr(boot_xor, 'find', lambda *args: decoder == 'xor')
    captured = []
    monkeypatch.setattr(boot_gzip, 'add', lambda *args: captured.append(args[4]) or 'game.unpacked.z64')
    add = code.add_segments

    def recover_first(config, workdir, image, log, *, skip_replacing_boot=False):
        if skip_replacing_boot:
            return image
        return add(config, workdir, image, log)

    monkeypatch.setattr(code, 'add_segments', recover_first)
    assert add(config, tmp_path, 'game.z64', lambda _: None) == 'game.unpacked.z64'
    assert captured == [recovered]
