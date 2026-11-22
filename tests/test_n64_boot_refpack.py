"""An OVLN executable needs both bounded compression and boot-loader evidence."""

import struct

from decompnet.consoles.n64 import boot_refpack, code


def test_boot_directory_needs_a_loader_source_and_indirect_handoff(monkeypatch):
    payload = bytes.fromhex('27bdfff0afbf000c03e0000827bd0010') + bytes(4)
    stream = b'\x10\xfb' + len(payload).to_bytes(3, 'big')
    stream += b''.join(b'\xe0' + payload[i:i+4] for i in range(0, len(payload), 4)) + b'\xfc'
    image = bytearray(0x3000)
    image.extend(b'OVLN' + struct.pack('>3I', 16+len(stream), 0x80010000, 0x80010004) + stream)
    calls = [{'kind': 'call', 'target': 0x80001000,
              'args': [{'kind': 'constant', 'value': 0x3000}, None, {'kind': 'constant', 'value': 32}]},
             {'kind': 'call', 'target': None, 'args': []}]
    monkeypatch.setattr(boot_refpack, 'read', lambda *_: {'events': calls})
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    ranges = [(0x1000, 0x1010, 0x80000400)]
    assert boot_refpack.find(image, ranges) == ((0x80010000, payload[:16], payload[16:]),)
    calls.pop()
    assert boot_refpack.find(image, ranges) is None
