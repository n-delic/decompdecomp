"""Synthetic coverage of a bounded boot flag-byte stream."""

import hashlib
import struct

from decompnet.consoles.n64 import boot_flag_lz


def test_literals_backreferences_and_end_marker():
    # Eight literals, then one overlapping two-byte copy and an end marker.
    stream = bytes.fromhex('0100000a 00 4142434445464748 c0 0102 0000')
    assert boot_flag_lz.decode(stream) == b'ABCDEFGHGH'
    assert boot_flag_lz.decode(stream[:-1]) is None
    assert boot_flag_lz.decode(bytes.fromhex('0100000a 80 1101')) is None
    assert boot_flag_lz.decode(bytes.fromhex('01000009 00 4142434445464748 c0 0102 0000')) is None


def test_extended_backreference_and_bad_size():
    stream = bytes.fromhex('01000019 00 4142434445464748 c0 0008 00 0000')
    assert boot_flag_lz.decode(stream) == b'ABCDEFGH' * 3 + b'A'
    assert boot_flag_lz.decode(bytes.fromhex('01000000 80 0000')) is None


def test_audited_loader_recovers_only_its_declared_program(monkeypatch):
    rom = bytearray(0x3000)
    struct.pack_into('>I', rom, 8, 0x80300000)
    words = (0x3c048030, 0x24840350, 0x40124800, 0x0c0c0020, 0,
             0x3c040001, 0x248416c0, 0x40064800, 0x0800c400, 0x02402821)
    struct.pack_into('>10I', rom, 0x1050, *words)
    monkeypatch.setattr(boot_flag_lz, '_DECODER_SHA256', hashlib.sha256(rom[0x1080:0x1298]).hexdigest())
    program = bytes.fromhex('27bdffe0 00000000 03e00008 00000000') * 20
    stream = b'\x01' + len(program).to_bytes(3, 'big')
    stream += b''.join(b'\0' + program[i:i+8] for i in range(0, len(program), 8)) + b'\x80\0\0'
    rom[0x1350:0x1350+len(stream)] = stream
    ranges = [(0x1000, 0x1350, 0x80300000)]
    found = boot_flag_lz.find(rom, ranges)
    assert found == ((0x80031000, program, b''),)
    rom[0x1080] ^= 1
    assert boot_flag_lz.find(rom, ranges) is None
