"""Table-declared banks retain original addresses, distinct identities and checked program bytes."""

import json
import struct
import zlib

import pytest
import yaml

from decompnet.consoles.n64 import overlays
from decompnet.rom import RomError, split
from decompnet.rom.symbols import Symbol, SymbolTable

from .test_n64code import _code


def cartridge():
    image = bytearray(0x6000)
    image[0x1800:0x1810] = overlays.MAGIC
    cursor = 0x2000
    for index in (1, 2, 3):
        program = _code(0x80300000, 8)
        payload = zlib.compress(program) if index != 3 else b'unknown storage'
        struct.pack_into('>4I', image, 0x1800 + index * 16,
                         0xB0000000 + cursor, len(payload), 0x80300000, 0x80300000 + len(program))
        image[cursor:cursor + len(payload)] = payload
        cursor += len(payload)
    return bytes(image)


def test_records_verify_zlib_and_leave_unknown_storage_opaque():
    result = overlays.read(cartridge())
    assert len(result) == 3
    assert result[0].data == result[1].data == _code(0x80300000, 8)
    assert result[0].address == result[1].address
    assert result[2].data is None
    assert overlays.read(bytes(0x6000)) == []


@pytest.mark.parametrize('change', ['checksum', 'size', 'range', 'bomb'])
def test_invalid_streams_and_table_ranges_fail_before_import(change):
    image = bytearray(cartridge())
    cart, packed, load, end = struct.unpack_from('>4I', image, 0x1810)
    if change == 'checksum':
        image[(cart & 0x0FFFFFFF) + packed - 1] ^= 1
    elif change == 'size':
        struct.pack_into('>I', image, 0x181C, end + 4)
    elif change == 'range':
        struct.pack_into('>I', image, 0x1810, 0xB0005FFF)
    else:
        struct.pack_into('>I', image, 0x181C, load + overlays.MAX_OUTPUT + 4)
    with pytest.raises(RomError):
        overlays.read(bytes(image))


def test_disassembly_keeps_same_address_functions_in_both_banks(tmp_path):
    original = cartridge()
    (tmp_path / 'rom.z64').write_bytes(original)
    config = tmp_path / 'splat.yaml'
    config.write_text(yaml.safe_dump({'options': {'basename': 'banks', 'target_path': 'rom.z64',
        'base_path': '.', 'platform': 'n64', 'compiler': 'KMC', 'o_as_suffix': True},
        'segments': [[0, 'bin', 'original'], [len(original)]]}))
    overlays.add(config, tmp_path, 'rom.z64', lambda *_: None)
    assert (tmp_path / 'rom.z64').read_bytes() == original
    cfg = yaml.safe_load(config.read_text())
    banks = [s for s in cfg['segments'] if isinstance(s, dict) and s.get('type') == 'code']
    assert len(banks) == 2 and banks[0]['vram'] == banks[1]['vram']
    assert banks[0]['start'] != banks[1]['start']
    split._splat(['split', config.name], tmp_path, lambda *_: None)
    functions = [split.parse_function(p, p.parent.name) for p in (tmp_path / 'asm/nonmatchings').glob('*/*.s')]
    functions = [f for f in functions if f]
    assert {f.segment for f in functions} == {'overlay_001', 'overlay_002'}
    assert len({f.name for f in functions}) == len(functions) >= 16
    at_start = [f for f in functions if f.vram == 0x80300000]
    assert len(at_start) == 2
    symbols = SymbolTable([Symbol(f.name, f.vram, f.size, 'func') for f in functions])
    assert all(symbols.resolve(f.name) == f.vram for f in functions)
    assert all('overlay_00' in f.name for f in functions)
    assert any('excluded' in note for note in json.loads((tmp_path / 'analysis-coverage.json').read_text()))


def test_names_at_a_shared_address_remain_resolvable():
    symbols = SymbolTable([Symbol('func_80300000_overlay_001', 0x80300000),
                           Symbol('func_80300000_overlay_002', 0x80300000)])
    assert len(symbols.by_name) == 2
    assert symbols.resolve('D_80300020_overlay_002') == 0x80300020
    assert symbols.resolve('func_80300000_overlay_001') == 0x80300000


def test_overlapping_export_stops_before_writing_files(tmp_path):
    from types import SimpleNamespace

    from decompnet.rom.export import ExportError, export_rom

    net = SimpleNamespace(_project=lambda _: {'id': 1},
                          store=SimpleNamespace(one=lambda *_: {'vram': 0x80300000}))
    with pytest.raises(ExportError, match='overlapping overlay banks'):
        export_rom(net, 'synthetic', tmp_path / 'export', None)
    assert not (tmp_path / 'export').exists()


def patched_cartridge():
    image = bytearray(cartridge())
    # Two zlib bases followed by a second load slot with the same complete size sequence.
    for index, records in [(3, [(3, 0x24, 0), (4, 0x42, 0)]), (4, [(7, 0x52, 0)])]:
        payload = b''.join(struct.pack('>HBB', *r) for r in records)
        offset = 0x4000 + index * 0x20
        struct.pack_into('>4I', image, 0x1800 + index * 16,
                         0xB0000000 + offset, len(payload), 0x80310000, 0x80310000 + len(_code(0x80300000, 8)))
        image[offset:offset + len(payload)] = payload
    return bytes(image)


def test_byte_patches_reconstruct_paired_bank_without_mutating_base():
    entries = overlays.read(patched_cartridge())
    assert len(entries) == 4
    assert entries[0].data == entries[1].data == _code(0x80300000, 8)
    expected = bytearray(entries[0].data)
    expected[3], expected[7] = 0x24, 0x42
    assert entries[2].data == bytes(expected)
    assert entries[2].storage == entries[3].storage == 'byte-patches'
    assert entries[3].data[7] == 0x52


def test_byte_patch_outside_base_fails():
    image = bytearray(patched_cartridge())
    struct.pack_into('>H', image, 0x4060, 0xffff)
    with pytest.raises(RomError, match='outside its base image'):
        overlays.read(bytes(image))


def test_unrecognized_patch_shape_remains_opaque():
    image = bytearray(patched_cartridge())
    image[0x4063] = 1
    assert all(e.data is None for e in overlays.read(bytes(image))[2:])
