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


def test_overlapping_programs_keep_their_own_text_boundaries():
    first, second = boot_chunked_bits.EXTRA_PROGRAMS[:2]
    assert first[4] == second[4]
    assert boot_chunked_bits.code_start(first[4], bytes(first[6])) == first[5]
    assert boot_chunked_bits.code_start(second[4], bytes(second[6])) == second[5]


def test_impossible_bss_is_dropped_without_changing_valid_segments():
    segments = [{'type': 'code', 'bss_size': 0x80000400}, {'type': 'code', 'bss_size': 0x1000}, [0x100]]
    boot_chunked_bits.drop_impossible_bss(segments)
    assert segments == [{'type': 'code'}, {'type': 'code', 'bss_size': 0x1000}, [0x100]]


def test_extra_program_requires_its_descriptor_and_full_digest(monkeypatch):
    rom = bytearray(0x1800)
    monkeypatch.setattr(boot_chunked_bits, '_LOADER', hashlib.sha256(rom[0x1000:0x16e0]).hexdigest())
    struct.pack_into('>I', rom, 0x16e8, 0xb0001740)
    struct.pack_into('>6I', rom, 0x1740, 0x54, 0x2000, 0x80000006, 0x5a, 0x2000, 0x80000006)
    data = bytes(80) + bytes.fromhex('27bdffe803e0000827bd0018') * 400 + bytes(0x2000-80-4800)
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(boot_chunked_bits, '_PRIMARY', digest)
    monkeypatch.setattr(boot_chunked_bits, 'EXTRA_PROGRAMS', ((1, 0x5a, 0x2000, 6, 0x80090000, 80, 4880, digest),))
    monkeypatch.setattr(boot_chunked_bits, 'decode', lambda source, expected: data)
    ranges = [(0x1000, 0x16e0, 0x80200400)]
    assert len(boot_chunked_bits.find(rom, ranges)) == 2
    rom[0x1753] ^= 1
    assert len(boot_chunked_bits.find(rom, ranges)) == 1


def test_variant_preserves_the_loader_entry_and_startup_data(monkeypatch):
    from decompnet.consoles.n64 import boot_chunked_bits

    rom = bytearray(0x1c00)
    monkeypatch.setattr(boot_chunked_bits, '_VARIANT', hashlib.sha256(rom[0x1000:0x1b00]).hexdigest())
    entry = dict(name='entry', start=0x1000, vram=0x80200400, subsegments=[[0x1000, 'hasm']])
    main = dict(name='main', start=0x105c, vram=0x8020045c, bss_size=0x38, subsegments=[[0x105c, 'c', 'main']])
    assert boot_chunked_bits.repair_variant_entry([entry, main], rom)
    assert (main['start'], main['vram'], main['subsegments'][0][0]) == (0x10fc, 0x802004fc, 0x10fc)
    assert 'bss_size' not in main
    assert not boot_chunked_bits.repair_variant_entry([entry, main], rom)
    text = bytes(96) + bytes.fromhex('27bdffe803e0000827bd0018') * 400 + bytes(128)
    monkeypatch.setattr(boot_chunked_bits, '_VARIANT_TEXT', hashlib.sha256(text).hexdigest())
    assert boot_chunked_bits.variant_sections(text) == (96, 0xa8, 0x5748)
    assert boot_chunked_bits.code_start(0x80000400, text) == 96
    assert boot_chunked_bits.variant_sections(text + b'\0') is None


def test_variant_removes_only_the_false_relocation_pair(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import boot_chunked_bits

    rom = bytearray(0x1b00)
    monkeypatch.setattr(boot_chunked_bits, '_VARIANT', hashlib.sha256(rom[0x1000:]).hexdigest())
    path = tmp_path / 'reloc_addrs.txt'
    first = 'rom:0x001030 reloc:MIPS_HI16 symbol:main_BSS_SIZE\n'
    second = 'rom:0x001038 reloc:MIPS_LO16 symbol:main_BSS_SIZE\n'
    keep = 'rom:0x001040 reloc:MIPS_LO16 symbol:other\n'
    path.write_text(first + keep, encoding='utf-8')
    assert not boot_chunked_bits.repair_variant_relocations(path, rom)
    path.write_text(first + keep + second, encoding='utf-8')
    assert boot_chunked_bits.repair_variant_relocations(path, rom)
    assert path.read_text(encoding='utf-8') == keep


def test_variant_programs_require_their_own_primary_and_descriptors(monkeypatch):
    rom = bytearray(0x1d00)
    monkeypatch.setattr(boot_chunked_bits, '_VARIANT', hashlib.sha256(rom[0x1000:0x1b00]).hexdigest())
    struct.pack_into('>6I', rom, 0x1b68, 0x54, 0x2000, 0x80000006, 0x5a, 0x2000, 0x80000006)
    data = bytes(96) + bytes.fromhex('27bdffe803e0000827bd0018') * 400 + bytes(0x2000-96-4800)
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(boot_chunked_bits, '_VARIANT_PRIMARY', digest)
    monkeypatch.setattr(boot_chunked_bits, 'VARIANT_PROGRAMS', ((1, 0x5a, 0x2000, 6, 0x80090000, 96, 4896, digest),))
    monkeypatch.setattr(boot_chunked_bits, 'decode', lambda source, expected: data)
    ranges = [(0x1000, 0x1b00, 0x80200400)]
    found = boot_chunked_bits.find(rom, ranges)
    assert len(found) == 2
    assert boot_chunked_bits.code_start(found[1][0], found[1][1]) == 96
    rom[0x1b7b] ^= 1
    assert len(boot_chunked_bits.find(rom, ranges)) == 1
