"""Synthetic resident and relocatable program storage."""

import struct
import zlib

from decompnet.consoles.n64 import resident_deflate


def raw_deflate(data):
    encoder = zlib.compressobj(wbits=-15)
    return encoder.compress(data)+encoder.flush()


def test_size_prefixed_stream_reads_only_its_own_bytes():
    program = bytes(range(32))
    member = b'\0\x02'+raw_deflate(program)
    assert resident_deflate._stream(member+b'next',0) == (len(member), program)
    assert resident_deflate._stream(b'\0\x01'+member[2:],0) is None
    assert resident_deflate._stream(member[:-1],0) is None


def test_overlay_checksums_and_directory_relocation_key():
    name = b'unit\0'
    prefix = bytearray(64)
    prefix[44:44+len(name)] = name
    key = 0x5a7c
    struct.pack_into('>H',prefix,52,key^1)
    text = struct.pack('>4I',0x0c000002,0,0x03e00008,0)
    program = prefix+text
    primary = checksum = 0
    for byte in program:
        primary += byte
        checksum ^= (byte << (primary&0x17)) & 0xffffffff
    header = struct.pack('>4IH',0x10000^primary,0,0x10001^checksum,len(name)<<8|0x82,5)
    before = bytearray(96)
    struct.pack_into('>H',before,0x46,key)
    member = header+raw_deflate(program)
    image = before+member
    decoded = resident_deflate._inflate_overlay(image,1,len(before),len(image))
    assert decoded == (text,16,0,0,(0,),[(0,1)])
    linked = resident_deflate._linked_overlay(text,16,0,[(0,1)])
    assert struct.unpack_from('>I',linked)[0] == 0x0c200002
    before[0x46] ^= 1
    assert resident_deflate._inflate_overlay(before+member,1,len(before),len(image)) is None
