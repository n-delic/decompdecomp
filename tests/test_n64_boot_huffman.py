"""The boot Huffman program needs both its loader and its expanded text proof."""

import hashlib
import struct

from decompassets.formats import AssetError
from decompnet.consoles.n64 import boot_gzip, boot_huffman


def fixture(monkeypatch):
    rom = bytearray(0x2b00)
    rom[0x1050:0x1f30] = bytes([0x12]) * (0x1f30 - 0x1050)
    rom[0x1f30:0x1f38] = b'Ogre\x10\x00\x00\x01'
    struct.pack_into('>II', rom, 0x1f38, 0x2ac0, 0x2b00)
    struct.pack_into('>I', rom, 0x2ac0, 0xec880)
    payload = b'code' + b'data'
    monkeypatch.setattr(boot_huffman, 'LOADER_DIGEST', hashlib.sha256(rom[0x1050:0x1f30]).hexdigest())
    monkeypatch.setattr(boot_huffman, 'TEXT_DIGEST', hashlib.sha256(payload[:4]).hexdigest())
    monkeypatch.setattr(boot_huffman, 'TEXT_END', 4)
    monkeypatch.setattr(boot_huffman.lzhuf, 'unpack', lambda source, size, fill: payload)
    return bytes(rom), [(0x1050, 0x1f30, 0x80000450)], payload


def test_boot_huffman_verifies_the_analysis_image(monkeypatch):
    rom, ranges, payload = fixture(monkeypatch)
    assert boot_huffman.find(rom, ranges) == ((0x80001ec0, b'code', b'data'),)
    assert boot_gzip.verify_analysis(rom, rom + payload, ranges)
    assert not boot_gzip.verify_analysis(rom, rom + payload[:-1] + b'!', ranges)
    assert boot_huffman.find(rom, [(0x1050, 0x1f30, 0x80001450)]) is None


def test_boot_huffman_rejects_changed_loader_descriptor_or_text(monkeypatch):
    rom, ranges, _ = fixture(monkeypatch)
    for position in (0x1050, 0x1f30, 0x1f3b, 0x2ac3):
        altered = bytearray(rom)
        altered[position] ^= 1
        assert boot_huffman.find(bytes(altered), ranges) is None
    monkeypatch.setattr(boot_huffman.lzhuf, 'unpack', lambda source, size, fill: b'bad!data')
    assert boot_huffman.find(rom, ranges) is None


def test_boot_huffman_rejects_broken_stream(monkeypatch):
    rom, ranges, _ = fixture(monkeypatch)
    def broken(*args, **kwargs):
        raise AssetError('truncated')
    monkeypatch.setattr(boot_huffman.lzhuf, 'unpack', broken)
    assert boot_huffman.find(rom, ranges) is None
