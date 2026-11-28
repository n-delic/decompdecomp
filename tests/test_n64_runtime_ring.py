"""Runtime ring streams need a known loader and source-backed RAM evidence."""

import hashlib
import random
import struct

from decompnet.consoles.n64 import runtime_ring, runtime_zlib


def _example(monkeypatch):
    rng = random.Random(91)
    payload = bytearray(rng.randbytes(0x12000))
    payload[0x100:0x10c] = bytes.fromhex('27bdfff0 03e00008 27bd0010')
    # Literal groups are followed by this loader's zero-pair terminator.
    stream = b''.join(b'\xff' + payload[i:i + 8] for i in range(0, len(payload), 8)) + b'\x00\x00\x00'
    source, base, loader = 0x4000, 0x80010000, 0x1800
    rom = bytearray(source + len(stream))
    # The boot calls the audited decoder with cartridge offset and output address.
    struct.pack_into('>4I', rom, 0x1000, 0x24044000, 0x3c058001, 0x0c000300, 0)
    rom[loader:loader + 676] = b'A' * 676
    monkeypatch.setattr(runtime_ring, '_LOADERS', {676: {hashlib.sha256(b'A' * 676).hexdigest()}})
    rom[source:] = stream
    ram = bytearray(0x30000)
    ram[0x10000:0x10000 + len(payload)] = payload
    return bytes(rom), bytes(payload), bytes(ram), {base + 0x100}, [(0x1000, 0x2000, 0x80000400)]


def test_ring_stream_recovery_requires_loader_and_observed_bytes(monkeypatch):
    rom, payload, ram, targets, banks = _example(monkeypatch)
    rows = runtime_ring.find(rom, [ram], targets, banks)
    assert rows == [dict(codec='ring_lzss', offset=0x4000, size=len(rom)-0x4000,
                         expanded=len(payload), vram=0x80010000, calls=sorted(targets))]
    changed = bytearray(ram); changed[0x18000] ^= 1
    assert runtime_ring.find(rom, [bytes(changed)], targets, banks) == []
    assert runtime_ring.find(rom, [ram], set(), banks) == []
    assert runtime_ring.find(rom, [ram], targets, banks + [(0, 16, 0x80010000)]) == []
    changed = bytearray(rom); changed[0x1800] ^= 1
    assert runtime_ring.find(bytes(changed), [ram], targets, banks) == []
    expanded = rom + bytes(-len(rom)%16+16) + payload
    expanded += bytes(-len(expanded)%16)
    assert runtime_zlib.verify_analysis(rom, expanded, rows)
    assert not runtime_zlib.verify_analysis(rom, expanded, [dict(rows[0], size=rows[0]['size']-1)])


def test_ring_stream_requires_a_terminator_and_bounds(monkeypatch):
    assert runtime_ring.decode(b'\x01A\x00') is None
    assert runtime_ring.decode(b'\x01A\x00\x00\x00') == (b'A', 4)
    assert runtime_ring.decode(b'\x07ABC\x01\x01\x00\x00') == (b'ABCABC', 8)
    monkeypatch.setattr(runtime_ring, '_LIMIT', 5)
    assert runtime_ring.decode(b'\x07ABC\x01\x01\x00\x00') is None
