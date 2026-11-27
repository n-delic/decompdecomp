"""Loader chunks must reproduce the original bytes before they can become code."""

import random
import struct

from decompnet.consoles.n64 import runtime_mio_chunks, runtime_zlib


def literal_chunk(data):
    masks = b'\xff' * ((len(data) + 7) // 8)
    masks += bytes(-len(masks) % 4)
    offset = 8 + len(masks)
    return struct.pack('>4H', 0, len(data), offset, offset) + masks + data


def test_chunk_decoder_carries_references_between_chunks():
    first = literal_chunk(b'ABCD').ljust(0x2000, b'\0')
    second = struct.pack('>4H', 0, 4, 12, 14) + bytes(4) + struct.pack('>H', 0x1003) + b'\0'
    rom = bytearray(0x1000) + first + second + b'\0'
    end = 0x1000 + len(first) + len(second)
    struct.pack_into('>6I', rom, 0x100, 0x1000, end, 8, end, end + 1, 1)
    row = {'codec':'chunked_mio0', 'table_offset':0x100, 'offset':0x1000, 'end':end, 'size':8}
    assert runtime_mio_chunks.decode(rom, row) == b'ABCDABCD'
    expanded = rom + bytes(-len(rom) % 16 + 16) + b'ABCDABCD'
    expanded += bytes(-len(expanded) % 16)
    assert runtime_zlib.verify_analysis(rom, expanded, [row])
    damaged = bytearray(rom)
    damaged[0x1000 + 0x2000 + 12:0x1000 + 0x2000 + 14] = b'\x1f\xff'
    assert runtime_mio_chunks.decode(damaged, row) is None
    assert not runtime_zlib.verify_analysis(damaged, expanded, [row])


def test_loader_table_requires_called_bytes_and_long_matching_ram():
    rng = random.Random(7)
    chunks = [bytes(rng.randrange(256) for _ in range(7000)) for _ in range(10)]
    encoded = b''.join(literal_chunk(part).ljust(0x2000, b'\0') for part in chunks)
    start, end = 0x2000, 0x2000 + len(encoded)
    rom = bytearray(start) + encoded + b'\0'
    struct.pack_into('>6I', rom, 0x1000, start, end, 70000, end, end + 1, 1)
    payload = b''.join(chunks)
    base = 0x80001000
    ram = bytearray(0x20000)
    ram[0x1000:0x1000 + len(payload)] = payload
    target = base + 0x100
    rows = runtime_mio_chunks.find(rom, [ram], {target})
    assert len(rows) == 1 and rows[0]['vram'] == base and rows[0]['calls'] == [target]
    altered = bytearray(ram)
    altered[0x1000 + 0x500] ^= 1
    assert runtime_mio_chunks.find(rom, [altered], {target}) == []
    assert runtime_mio_chunks.find(rom, [ram], {target}, [(0, len(payload), base)]) == []
    assert runtime_mio_chunks.find(rom, [ram], set()) == []
