"""A runtime-decoded analysis must be bounded and reconstruct its cartridge."""

import hashlib
import struct

import pytest
import yaml

from decompnet.consoles.n64 import runtime_byte_cipher as cipher
from decompnet.rom.split import code_ranges


def encoded_fixture(monkeypatch):
    image = bytearray(0x1000)
    loader, offset, base = 0x120, 0x300, 0x80001000
    address = base + offset - 0x100
    code = struct.pack('>5I', 0x27bdffe8, 0xafbf0014, 0x8fbf0014, 0x03e00008, 0x27bd0018) + bytes(12)
    key = sum(address.to_bytes(4, 'big')) & 0xff
    encoded = bytearray(code)
    for at in range(0, len(encoded), 4):
        encoded[at + 2] = (encoded[at + 2] + key) & 0xff
        encoded[at + 3] = (encoded[at + 3] - key) & 0xff
    image[offset:offset + len(encoded)] = encoded
    struct.pack_into('>II', image, loader, 0x3c070000 | ((address + 0x8000) >> 16),
                     0x24e70000 | (address & 0xffff))
    struct.pack_into('>I', image, loader + 0x58, 0x2ca10000 | len(encoded))
    struct.pack_into('>I', image, loader + 0x124, 0x0c000000 | ((address >> 2) & 0x3ffffff))
    monkeypatch.setattr(cipher, '_LOADER', hashlib.sha256(image[loader:loader + cipher._LOADER_SIZE]).hexdigest())
    return bytes(image), offset, address, code


def test_audited_code_is_decoded_and_verified(monkeypatch):
    image, offset, address, code = encoded_fixture(monkeypatch)
    ranges = [(0x100, offset + 4, 0x80001000)]
    assert cipher.find(image, ranges) == (offset, len(code), address, 20)
    decoded = cipher.analysis(image, ranges)
    assert decoded[offset:offset + len(code)] == code
    assert cipher.verify_analysis(image, decoded, ranges)
    changed = bytearray(decoded)
    changed[offset + 8] ^= 1
    assert not cipher.verify_analysis(image, bytes(changed), ranges)
    assert not cipher.verify_analysis(image, decoded[:-1] + b'\x01', ranges)
    assert not cipher.verify_analysis(image[:-1], decoded, ranges)


@pytest.mark.parametrize('case', ['loader', 'target', 'overlap', 'invalid_code'])
def test_ambiguous_or_changed_input_is_not_decoded(case, monkeypatch):
    image, offset, address, _ = encoded_fixture(monkeypatch)
    image = bytearray(image)
    ranges = [(0x100, offset + 4, 0x80001000)]
    if case == 'loader':
        image[0x150] ^= 1
    elif case == 'target':
        ranges = [(0x100, offset, 0x80001000)]
    elif case == 'overlap':
        ranges.append((offset + 8, offset + 16, address + 8))
    else:
        image[offset:offset + 32] = bytes(32)
    assert cipher.analysis(bytes(image), ranges) is None


def test_add_moves_only_the_proven_code_data_seam(tmp_path, monkeypatch):
    image, offset, _, code = encoded_fixture(monkeypatch)
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump({'options': {'target_path': 'rom.z64'}, 'segments': [
        {'name': 'main', 'type': 'code', 'start': 0x100, 'vram': 0x80001000,
         'subsegments': [[0x100, 'c', 'main'], [offset + 4, 'data'], [offset + 0x40, 'c', 'later']]},
        [len(image)],
    ]}), encoding='utf-8')
    (tmp_path / 'rom.z64').write_bytes(image)
    name = cipher.add(config, tmp_path, 'rom.z64', log=lambda _: None)
    assert name == 'rom.runtime-decoded.z64'
    assert (tmp_path / 'rom.z64').read_bytes() == image
    assert (tmp_path / name).read_bytes()[offset:offset + len(code)] == code
    assert yaml.safe_load(config.read_text())['segments'][0]['subsegments'][1] == [offset + 20, 'data']
    assert cipher.verify_analysis(image, (tmp_path / name).read_bytes(), code_ranges(config))


def test_add_declines_a_code_span_crossing_its_parent_mapping(tmp_path, monkeypatch):
    image, offset, _, _ = encoded_fixture(monkeypatch)
    config = tmp_path / 'game.yaml'
    original = yaml.safe_dump({'options': {'target_path': 'rom.z64'}, 'segments': [
        {'name': 'main', 'type': 'code', 'start': 0x100, 'vram': 0x80001000,
         'subsegments': [[0x100, 'c', 'main'], [offset + 4, 'data']]},
        [offset + 8],
    ]})
    config.write_text(original, encoding='utf-8')
    (tmp_path / 'rom.z64').write_bytes(image)
    assert cipher.add(config, tmp_path, 'rom.z64', log=lambda _: None) == 'rom.z64'
    assert config.read_text(encoding='utf-8') == original
