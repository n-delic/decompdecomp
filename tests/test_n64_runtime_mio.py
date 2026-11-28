"""MIO0 code is accepted only when cartridge and runtime evidence agree."""
import random

from crunch64 import mio0

from decompnet.consoles.n64 import runtime_mio, runtime_zlib


def test_runtime_mio_requires_independent_calls_and_reconstructs_cartridge():
    payload = random.Random(42).randbytes(0x12000)
    packed = mio0.compress(payload)
    rom = bytes(0x200) + packed + bytes(0x100)
    base = 0x80010000
    ram = bytes(0x10000) + payload + bytes(0x100)
    calls = {base+0x100, base+0x500, base+0x1000}
    rows = runtime_mio.find(rom, [ram], calls)
    assert len(rows) == 1
    assert runtime_zlib._payload(rom, rows[0]) == payload
    image = rom + bytes(-len(rom)%16+16) + payload
    image += bytes(-len(image)%16)
    assert runtime_zlib.verify_analysis(rom, image, rows)
    assert not runtime_zlib.verify_analysis(rom, image, [dict(rows[0], size=rows[0]['size']-1)])
    assert runtime_mio.find(rom, [ram], set(list(calls)[:2])) == []
    assert runtime_mio.find(rom, [ram], calls, [(0, 0x1000, base)]) == []
    changed = bytearray(ram)
    changed[0x18000] ^= 1
    assert runtime_mio.find(rom, [changed], calls) == []
    assert runtime_mio.find(rom+packed, [ram], calls) == []


def test_mio_rejects_invalid_or_truncated_streams():
    packed = mio0.compress(b'abcd'*100)
    assert runtime_mio.unpack(packed, 0)[0] == b'abcd'*100
    assert runtime_mio.unpack(packed[:-1], 0) is None
    assert runtime_mio.unpack(b'MIO0'+bytes(12), 0) is None
