"""A boot loader's byte-counted program stream, without game data."""
import struct

from decompnet.consoles.n64 import boot_byte_lz


def fixture():
    # Startup reserves 64 bytes for a runtime table, then enters CPU code.
    code = (bytes.fromhex('27bdffe803e0000827bd0018') * 400)
    decoded = bytes(64) + code + bytes(16)
    packed = bytearray(b'\x01\0\0\x10\0\x81\0\x3f\x01\0')
    remaining = code + bytes(16)
    for i in range(0, len(remaining), 127):
        chunk = remaining[i:i+127]
        packed.append(0x80 | len(chunk)); packed.extend(chunk)
    packed.append(0)
    rom = bytearray(0x12d0+0x48+len(packed))
    # The compact boot reads a directory-relative offset, calls the decoder,
    # rounds the loaded entry address and jumps through the output register.
    words = [0x3c108000,0x36100400,0x3c048020,0x248406d0,0x8c820000,
             0x3c058000,0x34a50400,0x0c080137,0x00442021,0x26100047,
             0x2402fff8,0x0c080180,0x02028024,0x0200f809,0]
    struct.pack_into(f'>{len(words)}I',rom,0x1010,*words)
    struct.pack_into('>4I',rom,0x10dc,0x90860005,0x00005021,0x10c0003b,0x240b0005)
    struct.pack_into('>4I',rom,0x12d0,0x48,len(decoded),0x80000000|len(packed),0x48+len(packed))
    rom[0x1318:]=packed
    return rom,decoded,bytes(packed)


def test_byte_lz_handles_overlapping_backreferences_and_exact_bounds():
    _,decoded,packed = fixture()
    assert boot_byte_lz.decode(packed,len(decoded)) == decoded
    assert boot_byte_lz.decode(packed[:-1],len(decoded)) is None
    assert boot_byte_lz.decode(packed,len(decoded)-1) is None
    broken=bytearray(packed);broken[8]=0xff
    assert boot_byte_lz.decode(broken,len(decoded)) is None


def test_boot_directory_requires_decoder_and_actual_indirect_entry():
    rom,decoded,_ = fixture()
    ranges=[(0x1010,0x12d0,0x80200410)]
    found = boot_byte_lz.find(rom,ranges)
    assert found and found[0][0] == 0x80000400
    assert found[0][1] + found[0][2] == decoded
    changed = bytearray(rom);struct.pack_into('>I',changed,0x1044,0)
    assert boot_byte_lz.find(changed,ranges) is None
    changed = bytearray(rom);changed[0x10df] ^= 1
    assert boot_byte_lz.find(changed,ranges) is None
