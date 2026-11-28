"""The byte-run boot program must match its loader, mapping and decoded image."""

import hashlib

from decompnet.consoles.n64 import boot_gzip, boot_paired_rle


def fixture(monkeypatch):
    source = 0x5000
    packed = b'\0\0\0\x0a\x84code\xe0'
    data = b'code\0\0'
    rom = bytearray(source + len(packed))
    rom[0x4424:0x4b10] = bytes([0x42]) * (0x4b10 - 0x4424)
    rom[source:] = packed
    monkeypatch.setattr(boot_paired_rle, 'SOURCE', source)
    monkeypatch.setattr(boot_paired_rle, 'PACKED', len(packed))
    monkeypatch.setattr(boot_paired_rle, 'SIZE', len(data))
    monkeypatch.setattr(boot_paired_rle, 'TEXT_END', 4)
    monkeypatch.setattr(boot_paired_rle, 'LOADER_DIGEST', hashlib.sha256(rom[0x4424:0x4b10]).hexdigest())
    monkeypatch.setattr(boot_paired_rle, 'OUTPUT_DIGEST', hashlib.sha256(data).hexdigest())
    return bytes(rom), [(0x1000, 0x5000, 0x80000400)], data


def test_boot_program_verifies_the_cartridge_and_analysis_image(monkeypatch):
    rom, ranges, data = fixture(monkeypatch)
    assert boot_paired_rle.find(rom, ranges) == ((0x80107830, b'code', b'\0\0'),)
    assert boot_gzip.verify_analysis(rom, rom + data, ranges)
    assert not boot_gzip.verify_analysis(rom, rom + data[:-1] + b'!', ranges)


def test_boot_program_rejects_bad_loader_mapping_and_stream(monkeypatch):
    rom, ranges, _ = fixture(monkeypatch)
    for at in (0x4424, 0x5003, 0x5005):
        changed = bytearray(rom)
        changed[at] ^= 1
        assert boot_paired_rle.find(bytes(changed), ranges) is None
    assert boot_paired_rle.find(rom, [(0x1000, 0x5000, 0x80010400)]) is None


def test_repeated_overlay_needs_its_complete_decoded_image(monkeypatch):
    rom, ranges, data = fixture(monkeypatch)
    source = 0x5010
    packed = b'\0\0\0\x0a\x84code\xe0'
    rom = bytearray(rom + bytes(source - len(rom)) + packed)
    monkeypatch.setattr(boot_paired_rle, 'OVERLAY_SOURCE', source)
    monkeypatch.setattr(boot_paired_rle, 'OVERLAY_PACKED', len(packed))
    monkeypatch.setattr(boot_paired_rle, 'OVERLAY_SIZE', len(data))
    monkeypatch.setattr(boot_paired_rle, 'OVERLAY_TEXT_END', 4)
    monkeypatch.setattr(boot_paired_rle, 'OVERLAY_DIGEST', hashlib.sha256(data).hexdigest())
    result = boot_paired_rle.find(bytes(rom), ranges)
    assert [program[0] for program in result] == [0x80107830, 0x801bf1a0, 0x801fa948]
    assert boot_gzip.verify_analysis(bytes(rom), bytes(rom) + data * 3, ranges)
    rom[source + 5] ^= 1
    assert len(boot_paired_rle.find(bytes(rom), ranges)) == 1
