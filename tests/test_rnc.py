"""Synthetic containers only: no cartridge bytes or extracted game code."""
import struct

import pytest

from decompnet.consoles.n64 import rnc


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.bit = 16
        self.word = 0

    def put(self, value, count):
        for bit in range(count):
            if self.bit == 16:
                self.word = len(self.data)
                self.data += b'\0\0'
                self.bit = 0
            self.data[self.word + self.bit // 8] |= ((value >> bit) & 1) << (self.bit % 8)
            self.bit += 1

    def tree(self, lengths):
        self.put(len(lengths), 5)
        for length in lengths:
            self.put(length, 4)

    def container(self, output):
        payload = bytes(self.data)
        return (b'RNC\x01' + struct.pack('>IIHHBB', len(output), len(payload),
                rnc.crc16(output), rnc.crc16(payload), 0, 1) + payload)


def literal_block(output):
    n = len(output)
    symbol = n.bit_length() if n >= 2 else n
    w = Writer()
    w.put(0, 2)
    w.tree([0] * symbol + [1])
    w.tree([])
    w.tree([])
    w.put(1, 16)
    w.put(0, 1)
    if symbol >= 2:
        w.put(n - (1 << (symbol - 1)), symbol - 1)
    w.data += output
    return w.container(output)


@pytest.mark.parametrize('size', [1, 2, 3, 17, 256, 4096, 131072])
def test_literals(size):
    output = bytes(i % 251 for i in range(size))
    locations = []
    block = literal_block(output)
    assert rnc.unpack(block, literals=locations) == output
    assert len(locations) == 1
    span = locations[0]
    assert block[span.packed:span.packed + span.size] == output
    assert span.output == 0


def overlap_block():
    w = Writer()
    w.put(0, 2)
    w.tree([1, 0, 1])
    w.tree([0, 0, 1])
    w.tree([0, 0, 0, 1])
    w.put(2, 16)
    w.put(1, 1)  # literal symbol 2
    w.put(1, 1)  # literal length 3
    w.data += b'abc'
    w.put(0, 1)  # distance symbol 2
    w.put(0, 1)  # distance 2 + 1
    w.put(0, 1)  # length symbol 3
    w.put(0, 2)  # length 4 + 2
    w.put(0, 1)  # final zero-length literal
    return w.container(b'abcabcabc')


def test_overlapping_backreference():
    assert rnc.unpack(overlap_block()) == b'abcabcabc'
    assert rnc.unpack(rnc.repack_like(b'xyzxyzxyz', overlap_block())) == b'xyzxyzxyz'
    with pytest.raises(rnc.RncError):
        rnc.repack_like(b'abcabcabd', overlap_block())


def test_rejects_corruption_and_limits():
    block = literal_block(b'hello world')
    with pytest.raises(rnc.RncError, match='sizes'):
        rnc.unpack(block, max_output=3)
    with pytest.raises(rnc.RncError, match='sizes'):
        rnc.unpack(block[:-1])
    damaged = bytearray(block)
    damaged[-1] ^= 1
    with pytest.raises(rnc.RncError, match='packed checksum'):
        rnc.unpack(damaged)
    damaged = bytearray(block)
    damaged[12] ^= 1
    with pytest.raises(rnc.RncError, match='unpacked checksum'):
        rnc.unpack(damaged)


def test_template_uses_new_literals():
    original = literal_block(b'hello world')
    rebuilt = rnc.repack_like(b'other words', original)
    assert rebuilt != original
    assert rnc.unpack(rebuilt) == b'other words'
    assert rnc.repack_like(b'hello world', original) == original
    with pytest.raises(rnc.RncError, match='size'):
        rnc.repack_like(b'short', original)


def test_import_discovers_code_and_rejects_assets():
    from decompnet.consoles.n64.code import unpack_code

    # Invented MIPS stubs, with enough prologues and returns to exercise code detection.
    stub = bytes.fromhex('27bdfff0 afbf000c 8fbf000c 27bd0010 03e00008 00000000')
    code = stub * 256
    asset = literal_block(b'A' * 4096)
    packed = literal_block(code)
    image = bytes(32) + asset + bytes(32) + packed
    assert unpack_code(image) == [(32 + len(asset) + 32, code)]


def test_rebuild_uses_linked_payload_and_preserves_prefix_changes():
    block = literal_block(b'hello world')
    original = b'HEAD' + block
    analysis = original + b'other words'
    mapping = [{'rom': 4, 'analysis': len(original), 'size': 11}]
    rebuilt = rnc.rebuild_cartridge(analysis, original, mapping)
    assert rnc.unpack(rebuilt[4:]) == b'other words'
    changed = b'EDIT' + analysis[4:]
    assert rnc.rebuild_cartridge(changed, original, mapping)[:4] == b'EDIT'
    assert rnc.rebuild_cartridge(original + b'hello world', original, mapping) == original
