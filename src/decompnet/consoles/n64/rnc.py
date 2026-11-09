"""RNC method 1 containers: bounded Huffman/LZ unpacking of code held in a cartridge image.

Control bits are little-endian words interleaved with literal bytes. The reader must discard prefetched literal
bytes after each run, keeping only the unused control bits. Both container checksums are checked before code is used.
"""

from __future__ import annotations

import functools
import struct
from dataclasses import dataclass

HEADER_BYTES = 18
MAX_OUTPUT = 4 << 20
MAX_CODE_BITS = 16


class RncError(ValueError):
    pass


@functools.cache
def _crc_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
        table.append(value)
    return tuple(table)


def crc16(data: bytes) -> int:
    value = 0
    table = _crc_table()
    for byte in data:
        value = (value >> 8) ^ table[(value ^ byte) & 0xFF]
    return value


@dataclass
class Literal:
    packed: int
    output: int
    size: int


class _Bits:
    def __init__(self, payload: bytes):
        self.data = payload + b"\0\0"
        self.end = len(payload)
        self.pos = 0
        self.buffer = self.word(0)
        self.left = 0

    def word(self, pos: int) -> int:
        if pos + 2 > len(self.data):
            raise RncError("truncated RNC control bits")
        return self.data[pos] | self.data[pos + 1] << 8

    def take(self, count: int) -> int:
        if not 0 <= count <= 30:
            raise RncError("invalid RNC bit count")
        if count > 16:
            return self.take(16) | self.take(count - 16) << 16
        if not count:
            return 0
        value = self.buffer & ((1 << count) - 1)
        if count > self.left:
            self.buffer >>= self.left
            self.pos += 2
            self.buffer |= self.word(self.pos) << 16
            count -= self.left
            self.left = 16
        self.buffer >>= count
        self.left -= count
        return value

    def literal(self, count: int) -> bytes:
        if count < 0 or self.pos + count > self.end:
            raise RncError("truncated RNC literal run")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        self.buffer &= (1 << self.left) - 1
        self.buffer |= self.word(self.pos) << self.left
        return result


def _table(bits: _Bits) -> list[tuple[int, int, int, int]]:
    lengths = [bits.take(4) for _ in range(bits.take(5))]
    entries = []
    code = 0
    for width in range(1, MAX_CODE_BITS + 1):
        for symbol, length in enumerate(lengths):
            if length != width:
                continue
            if code >= 1 << width:
                raise RncError("oversubscribed RNC Huffman tree")
            reversed_code = int(f"{code:0{width}b}"[::-1], 2)
            entries.append(((1 << width) - 1, reversed_code, width, symbol))
            code += 1
        code <<= 1
    return entries


def _value(bits: _Bits, table: list[tuple[int, int, int, int]]) -> int:
    for mask, code, width, symbol in table:
        if bits.buffer & mask == code:
            bits.take(width)
            return symbol if symbol < 2 else (1 << (symbol - 1)) | bits.take(symbol - 1)
    raise RncError("RNC stream has no matching Huffman code")


def unpack(data: bytes, *, max_output: int = MAX_OUTPUT, literals: list[Literal] | None = None) -> bytes:
    """Unpack a checksummed method-1 block. Optional literal locations support a verified compression template."""
    if len(data) < HEADER_BYTES or data[:4] != b"RNC\x01":
        raise RncError("not an RNC method-1 container")
    size, packed, unpacked_crc, packed_crc = struct.unpack_from(">IIHH", data, 4)
    if not 0 < size <= max_output or packed < 2 or HEADER_BYTES + packed > len(data):
        raise RncError("invalid RNC sizes")
    payload = data[HEADER_BYTES:HEADER_BYTES + packed]
    if crc16(payload) != packed_crc:
        raise RncError("RNC packed checksum differs")
    bits = _Bits(payload)
    if bits.take(2):
        raise RncError("encrypted or locked RNC streams are unsupported")
    out = bytearray()
    chunks = 0
    while len(out) < size:
        raw, distance, length = _table(bits), _table(bits), _table(bits)
        count = bits.take(16)
        if not count:
            raise RncError("empty RNC chunk")
        for index in range(count):
            n = _value(bits, raw)
            if len(out) + n > size:
                raise RncError("RNC literal run exceeds declared output")
            if n:
                if literals is not None:
                    literals.append(Literal(HEADER_BYTES + bits.pos, len(out), n))
                out += bits.literal(n)
            if index + 1 < count:
                offset, n = _value(bits, distance) + 1, _value(bits, length) + 2
                if offset > len(out) or len(out) + n > size:
                    raise RncError("RNC back-reference is outside its output")
                for _ in range(n):
                    out.append(out[-offset])  # a run can overlap its own already-decoded bytes
        chunks += 1
        if chunks > 255:
            raise RncError("too many RNC chunks")
    if crc16(out) != unpacked_crc:
        raise RncError("RNC unpacked checksum differs")
    return bytes(out)


def repack_like(output: bytes, template: bytes) -> bytes:
    """Reuse a valid stream's coding decisions, deriving its literal bytes from newly built output.

    This is a matching-build operation, not a general compressor. Edits incompatible with the template's LZ
    references are rejected by decoding the result and comparing every output byte.
    """
    locations: list[Literal] = []
    original = unpack(template, literals=locations)
    if len(output) != len(original):
        raise RncError("RNC template requires the original output size")
    packed = int.from_bytes(template[8:12], "big")
    result = bytearray(template[:HEADER_BYTES + packed])
    for span in locations:
        result[span.packed:span.packed + span.size] = output[span.output:span.output + span.size]
    struct.pack_into(">HH", result, 12, crc16(output), crc16(result[HEADER_BYTES:]))
    if unpack(result) != output:
        raise RncError("new output is incompatible with RNC template references")
    return bytes(result)


def rebuild_cartridge(analysis: bytes, original: bytes, blocks: list[dict]) -> bytes:
    """Replace packed code with literals derived from a linked analysis image; preserve rebuilt prefix bytes."""
    if len(analysis) < len(original):
        raise RncError("analysis image is shorter than the cartridge")
    result = bytearray(analysis[:len(original)])
    for block in blocks:
        offset, start, size = block['rom'], block['analysis'], block['size']
        if not 0 <= offset <= len(original) - HEADER_BYTES or not len(original) <= start <= len(analysis) - size:
            raise RncError("compressed-block mapping is outside its images")
        packed = repack_like(analysis[start:start + size], original[offset:])
        result[offset:offset + len(packed)] = packed
    return bytes(result)
