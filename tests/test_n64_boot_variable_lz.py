"""The boot format accepts bounded copies only when loader evidence agrees."""
import struct

from decompnet.consoles.n64 import boot_gzip, boot_variable_lz


def test_literals_and_overlapping_backreferences():
    # Four literal bytes followed by a six-byte overlapping repeat.
    assert boot_variable_lz.decode(b'\x83ABCD\x43', 10) == (b'ABCDABCDAB', 6)
    assert boot_variable_lz.decode(b'\x00', 2) is None
    assert boot_variable_lz.decode(b'\x84AB', 5) is None
    assert boot_variable_lz.decode(b'\xff'*40, 1) is None
    assert boot_variable_lz.decode(b'\x83ABCD\x43', 9) is None


def test_loader_signature_and_entry_are_required(monkeypatch):
    from decompnet.consoles.n64 import code

    rom = bytearray(0x3000)
    signatures = [
        '91040000000416000440001725080001000519003082000f00432827000618c0',
        '2c8200901040000e000611003083000f004310212446000150c0001900004821',
        '2c8200a0104000043082000c308600030808016b000228822c8200c010400004',
    ]
    for i, signature in enumerate(signatures):
        data = bytes.fromhex(signature)
        rom[0x1100+i*0x40:0x1100+i*0x40+len(data)] = data
    # A complete frame, return and delay slot as literals.
    program = bytes.fromhex('27bdfff003e0000827bd0010')
    rom[0x1300:0x1304] = struct.pack('>I', len(program))
    rom[0x1304:0x1311] = bytes([0x8b])+program
    events = [dict(kind='load', instruction=0x80200080, width=4,
                   place=dict(kind='constant', value=0x80200300)),
              dict(kind='call', instruction=0x80200280, target=0x80000400)]
    monkeypatch.setattr(boot_variable_lz, 'read', lambda *_: dict(events=events))
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    ranges = [(0x1000, 0x1300, 0x80200000)]
    assert boot_variable_lz.find(rom, ranges) == ((0x80000400, program, b''),)
    assert boot_gzip.find(rom, ranges) == ((0x80000400, program, b''),)
    rom[0x1100] ^= 1
    assert boot_variable_lz.find(rom, ranges) is None


def test_startup_instructions_and_data_are_separated_from_called_code(monkeypatch):
    from decompnet.consoles.n64 import code

    data = bytearray(0x5000)
    vram = 0x80000400
    struct.pack_into('>5I', data, 0, 0x3c1d803f, 0x0c000000 | ((vram+0x100) >> 2 & 0x3ffffff), 0, 0, 0x0000000d)
    struct.pack_into('>5I', data, 0x100, 0x27bdfff0, 0xafbf000c, 0x8fbf000c, 0x03e00008, 0x27bd0010)
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    assert boot_variable_lz.startup_sections(bytes(data), vram) == (20, 0x100)
    struct.pack_into('>I', data, 4, 0)
    assert boot_variable_lz.startup_sections(bytes(data), vram) is None
