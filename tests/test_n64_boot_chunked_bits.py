"""A chunked boot stream, with all game bytes replaced by synthetic data."""
import hashlib
import struct

from decompnet.consoles.n64 import boot_chunked_bits


def bits(value):
    value += '0' * (-len(value) % 8)
    return bytes(int(value[i:i+8], 2) for i in range(0, len(value), 8))


def test_literals_and_overlapping_backreference_are_bounded():
    # Three literal bytes, then a length-three copy at distance three.
    payload = '1' + f'{3:07b}' + ''.join(f'{b:08b}' for b in b'abc') + '0' + '1' + '11'
    stream = b'\x03\0\0\x10\0' + bits(payload)
    assert boot_chunked_bits.decode(stream, 6) == b'abcabc'
    assert boot_chunked_bits.decode(stream[:-1], 6) is None
    assert boot_chunked_bits.decode(stream, 5) is None
    assert boot_chunked_bits.decode(b'bad!!' + bits(payload), 6) is None
    assert boot_chunked_bits.decode(b'\x03\0\0\x10\0' + bits('0111'), 3) is None


def test_loader_requires_full_identity_directory_and_valid_code(monkeypatch):
    rom = bytearray(0x1800)
    monkeypatch.setattr(boot_chunked_bits, '_LOADER', hashlib.sha256(rom[0x1000:0x16e0]).hexdigest())
    struct.pack_into('>I', rom, 0x16e8, 0xb0001740)
    struct.pack_into('>4I', rom, 0x1740, 0x54, 0x2000, 0x80000006, 0x5a)
    data = bytes(80) + bytes.fromhex('27bdffe803e0000827bd0018') * 400 + bytes(0x2000-80-4800)
    monkeypatch.setattr(boot_chunked_bits, 'decode', lambda source, expected: data)
    ranges = [(0x1000, 0x1100, 0x80200400), (0x1100, 0x16e0, 0x80200500)]
    found = boot_chunked_bits.find(rom, ranges)
    assert found and found[0][1] + found[0][2] == data
    changed = bytearray(rom); changed[0x1010] = 1
    assert boot_chunked_bits.find(changed, ranges) is None
    changed = bytearray(rom); changed[0x174f] ^= 2
    assert boot_chunked_bits.find(changed, ranges) is None
    assert boot_chunked_bits.find(rom, ranges[:1]) is None


def test_window_limit_is_preserved_across_input_pages():
    literal = b'\xff' + b'A' * 127
    first = b'\x03\0\0\x10\0' + literal * 31 + b'\xfa' + b'A' * 122
    assert len(first) == 4096
    pages = literal * (32 * 16)
    # At more than 65536 output bytes, distance uses all 16 bits.
    stream = first + pages + bits('0' + '1' + f'{1:016b}')
    expected = 31 * 127 + 122 + 32 * 16 * 127 + 3
    assert boot_chunked_bits.decode(stream, expected) == b'A' * expected
