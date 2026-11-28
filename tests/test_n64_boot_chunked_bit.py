"""A chunked boot program needs bounded decoding and exact loader evidence."""

import hashlib
import struct

from decompnet.consoles.n64 import boot_chunked_bit, boot_gzip


def test_literal_chunk_and_rejected_streams():
    assert boot_chunked_bit.decode(b'\x03\0\0\0\0\x83abc', 3) == b'abc'
    assert boot_chunked_bit.decode(b'\x03\0\0\0\0\x83abc\x70', 6) == b'abcabc'
    assert boot_chunked_bit.decode(b'\x02\0\0\0\0\x83abc', 3) is None
    assert boot_chunked_bit.decode(b'\x03\0\0\0\0\x83ab', 3) is None
    assert boot_chunked_bit.decode(b'\x03\0\0\0\0\0\0\0\0', 8) is None


def test_literal_runs_cross_the_input_chunk_boundary():
    first = b'\x03\0\0\0\0' + (b'\xff' + b'A' * 127) * 31 + b'\xfa' + b'B' * 122
    assert len(first) == 0x1000
    assert boot_chunked_bit.decode(first + b'\x81Z', 31 * 127 + 123) == b'A' * (31 * 127) + b'B' * 122 + b'Z'


def test_impossible_bss_is_dropped_without_changing_valid_segments():
    segments = [{'type': 'code', 'bss_size': 0x80000400}, {'type': 'code', 'bss_size': 0x1000}, [0x100]]
    boot_chunked_bit.drop_impossible_bss(segments)
    assert segments == [{'type': 'code'}, {'type': 'code', 'bss_size': 0x1000}, [0x100]]


def test_overlapping_programs_keep_their_own_text_boundaries():
    first = boot_chunked_bit.EXTRA_PROGRAMS[0]
    second = boot_chunked_bit.EXTRA_PROGRAMS[1]
    assert first[4] == second[4]
    assert boot_chunked_bit.code_start(first[4], bytes(first[6])) == first[5]
    assert boot_chunked_bit.code_start(second[4], bytes(second[6])) == second[5]


def test_boot_image_requires_loader_mapping_and_output_proof(monkeypatch):
    rom = bytearray(0x34e88)
    rom[0x1000:0x16e0] = bytes([0x42]) * 0x6e0
    struct.pack_into('>III', rom, 0x1740, 0x54, 0x61578, 0x800336f4)
    payload = b'code' + b'data'
    monkeypatch.setattr(boot_chunked_bit, 'LOADER_DIGEST', hashlib.sha256(rom[0x1000:0x16e0]).hexdigest())
    monkeypatch.setattr(boot_chunked_bit, 'OUTPUT_DIGEST', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(boot_chunked_bit, 'CODE_END', 4)
    monkeypatch.setattr(boot_chunked_bit, 'EXTRA_PROGRAMS', ())
    monkeypatch.setattr(boot_chunked_bit, 'decode', lambda source, expected: payload)
    rom = bytes(rom)
    ranges = [(0x1000, 0x16e0, 0x80200400)]
    assert boot_chunked_bit.find(rom, ranges) == ((0x80000400, b'code', b'data'),)
    assert boot_chunked_bit.find(rom, [(0x1000, 0x1100, 0x80200400),
                                      (0x1100, 0x16e0, 0x80200500)]) == ((0x80000400, b'code', b'data'),)
    assert boot_gzip.verify_analysis(rom, rom + payload, ranges)
    assert not boot_gzip.verify_analysis(rom, rom + payload[:-1] + b'!', ranges)
    for at in (0x1000, 0x1743, 0x1747, 0x174b):
        changed = bytearray(rom)
        changed[at] ^= 1
        assert boot_chunked_bit.find(bytes(changed), ranges) is None
    assert boot_chunked_bit.find(rom, [(0x1000, 0x16e0, 0x80210400)]) is None
    monkeypatch.setattr(boot_chunked_bit, 'decode', lambda source, expected: b'bad!data')
    assert boot_chunked_bit.find(rom, ranges) is None
