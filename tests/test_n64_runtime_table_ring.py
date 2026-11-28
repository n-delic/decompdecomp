"""A loaded, length-prefixed ring bank is only recovered with RAM evidence."""

import struct

from decompnet.consoles.n64 import runtime_table_ring, runtime_zlib


def _literal(payload):
    return struct.pack('>I', len(payload)) + b''.join(b'\xff' + payload[i:i + 8]
                                                        for i in range(0, len(payload), 8))


def test_decode_bounds_and_ring_copy():
    assert runtime_table_ring.decode(b'\0\0\0\x06\x07ABC\xbe\xc0') == (b'ABCABC', 10)
    assert runtime_table_ring.decode(b'\0\0\0\x04\x01A') is None
    assert runtime_table_ring.decode(b'\0\0\0\x02\0\0\0') is None


def test_directory_bank_requires_exact_ram_and_internal_calls():
    base = 0x80004000
    payload = bytearray(256)
    for index in range(4):
        struct.pack_into('>I', payload, index * 4, 0x0c000000 | ((base + 64 + index * 4) >> 2 & 0x3ffffff))
    payload[80:208] = bytes(range(128))
    encoded = _literal(payload)
    table = 0x100
    start = table + 0x2008
    rom = bytearray(start + len(encoded) + 16)
    rom[table:table + 8] = runtime_table_ring._HEADER
    struct.pack_into('>II', rom, table + 8, 0, len(encoded))
    struct.pack_into('>II', rom, table + 16, 0xffffffff, 0xffffffff)
    rom[start:start + len(encoded)] = encoded
    ram = bytearray(0x10000)
    ram[0x4000:0x4100] = payload
    rows = runtime_table_ring.find(bytes(rom), [bytes(ram)])
    assert len(rows) == 1 and rows[0]['vram'] == base
    expanded = bytes(rom) + bytes(-len(rom) % 16 + 16) + payload
    expanded += bytes(-len(expanded) % 16)
    assert runtime_zlib.verify_analysis(bytes(rom), expanded, rows)
    assert not runtime_table_ring.find(bytes(rom), [bytes(ram)], [(0, 8, base)])
    ram[0x40ff] ^= 1
    assert not runtime_table_ring.find(bytes(rom), [bytes(ram)])
    assert not runtime_zlib.verify_analysis(bytes(rom), expanded, [dict(rows[0], size=rows[0]['size'] - 1)])


def test_overlapping_banks_keep_scoped_trap_symbols(tmp_path, monkeypatch):
    import yaml

    from decompnet.consoles.n64 import loader_code

    base = 0x80043000
    payloads = []
    for bank in range(2):
        payload = bytearray(256)
        payload[0] = bank
        for i in range(8):
            struct.pack_into('>II', payload, 64 + i * 8, 8, 0x24080000 + i)
        payloads.append(bytes(payload))
    streams = [_literal(payload) for payload in payloads]
    rom = streams[0] + streams[1]
    rows = [dict(codec='table_ring', offset=offset, size=len(stream), expanded=256,
                 vram=base, calls=[base + 64])
            for offset, stream in ((0, streams[0]), (len(streams[0]), streams[1]))]
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump({'options': {}, 'segments': [
        {'name': 'low', 'type': 'code', 'start': 0, 'vram': 0x80001000,
         'subsegments': [[0, 'c', 'low']]},
        {'name': 'high', 'type': 'code', 'start': 16, 'vram': 0x80200000,
         'subsegments': [[16, 'c', 'high']]}, [len(rom)]]}))
    monkeypatch.setattr(loader_code, 'complete', lambda image, _, ranges, *args, **kwargs:
                        [(ranges[0][0] + i * 4, ranges[0][0] + i * 4 + 4, base + i * 4)
                         for i in range(4)])
    image = runtime_zlib.add(config, tmp_path, 'rom.z64', rom, [], rows, lambda _: None)
    assert image and runtime_zlib.verify_analysis(rom, (tmp_path / image).read_bytes(), rows)
    cfg = yaml.safe_load(config.read_text())
    segments = {s['name']: s for s in cfg['segments'] if isinstance(s, dict) and s.get('name')}
    assert segments['low']['exclusive_ram_id'] == 'runtime_resident_low'
    assert segments['runtime_program_0000']['exclusive_ram_id'] == segments['runtime_program_0001']['exclusive_ram_id']
    symbols = (tmp_path / 'symbol_addrs.txt').read_text()
    assert 'runtime_program_0000_trap_0000' in symbols
    assert 'runtime_program_0001_trap_0000' in symbols
    assert symbols.count('segment:runtime_program_0000') == 8
    assert symbols.count('segment:runtime_program_0001') == 8
