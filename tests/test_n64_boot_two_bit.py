"""A compressed program needs a boot-referenced directory and a call into its output."""
import struct

from decompnet.consoles.n64 import boot_two_bit, code


def test_directory_must_be_loaded_and_called(monkeypatch):
    image = bytearray(0x7000)
    directory, source, target, size = 0x3000, 0x3050, 0x80010000, 0x1000
    struct.pack_into('>4I', image, directory, source, target, size, 0)
    struct.pack_into('>4I', image, directory+16, 0x6000, target, 0x1000, 0)
    struct.pack_into('>I', image, source, size)
    payload = bytes(16)+bytes.fromhex('27bdfff0afbf000c03e0000827bd0010')+bytes(size-32)
    calls = [{'kind': 'call', 'target': 0x80001000, 'args': [
        {'kind': 'constant', 'value': directory}, {'kind': 'constant', 'value': 0x80100000},
        {'kind': 'constant', 'value': 80}]},
        {'kind': 'call', 'target': target+16, 'args': []}]
    monkeypatch.setattr(boot_two_bit, 'read', lambda *_: {'events': calls})
    monkeypatch.setattr(boot_two_bit, 'unpack', lambda _: (payload, 16))
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    ranges = [(0x1000, 0x1100, 0x80000400)]
    assert boot_two_bit.find(image, ranges) == ((target, payload[:32], payload[32:]),)
    calls.pop()
    assert boot_two_bit.find(image, ranges) is None
    calls.append({'kind': 'call', 'target': target+16, 'args': []})
    image[directory+12] = 1
    assert boot_two_bit.find(image, ranges) is None
