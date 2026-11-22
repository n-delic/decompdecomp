import struct
import zlib

from decompnet.consoles.n64 import boot_raw_deflate, code


def fixture():
    image = bytearray(0x4000)
    struct.pack_into('>3I', image, 0x1000, 0x3c06e000, 0x3c04a460, 0x0c000100)
    header = 0x1100
    struct.pack_into('>I', image, header, 2)
    image[header+28:header+44] = b'game'+bytes(12)
    image[header+76:header+92] = b'other'+bytes(11)
    body = bytes(48)+bytes.fromhex('03e0000800000000')+bytes(8)
    encoder = zlib.compressobj(wbits=-15)
    packed = encoder.compress(body)+encoder.flush()
    struct.pack_into('>6I', image, header+44, 0x2000, len(packed), 0xe0000400, 0, 0xe0000440, 0)
    image[0x2000:0x2000+len(packed)] = packed
    return image, body, [(0x1000, 0x1010, 0x80200000)]


def test_named_table_requires_boot_alias_and_complete_inflate(monkeypatch):
    image, body, boot = fixture()
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    decoded = boot_raw_deflate._table_find(image, boot)
    assert decoded == ((0x80000400, body[:56], body[56:]),)
    image[0x1002] ^= 1
    assert boot_raw_deflate._table_find(image, boot) is None
    image, _, _ = fixture()
    image[0x2001] ^= 0xff
    assert boot_raw_deflate._table_find(image, boot) is None
