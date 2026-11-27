import struct

from decompnet.consoles.n64 import boot_compact_mio


def test_compact_chunks_can_reference_output_from_prior_chunk():
    first = struct.pack('>4H', 0, 3, 9, 9) + b'\xe0abc'
    first += bytes(0x2000-len(first))
    second = struct.pack('>4H', 0, 3, 9, 11) + b'\0\0\2\0'
    image = first + second
    assert boot_compact_mio.unpack(image, 0, len(image), 6) == b'abcabc'
    assert boot_compact_mio.unpack(image, 0, len(image)-2, 6) is None
    assert boot_compact_mio.unpack(image, 0, len(image), 5) is None


def test_compact_decoder_rejects_invalid_backreference():
    chunk = struct.pack('>4H', 0, 3, 9, 11) + b'\0\0\2\0'
    assert boot_compact_mio.unpack(chunk, 0, len(chunk), 3) is None


def test_loader_metadata_recovers_the_original_program(monkeypatch):
    from decompnet.consoles.n64 import code

    monkeypatch.setattr(code, '_looks_like_code', lambda data: True)
    begin, text_size, data_size = 0x80010000, 60000, 10000
    image = bytearray(0x10000 + 12*0x2000)
    # Construct-MIO magic and a boot call into the decoded text.
    struct.pack_into('>3I', image, 0x1000, 0x3c0e4d49, 0x35ce4f30,
                     0x0c000000 | ((begin+0x20) >> 2 & 0x03ffffff))
    image[0x4000:0x4010] = b'entry\0' + bytes(10)
    struct.pack_into('>6I', image, 0x4010, 0, 0, begin, begin+text_size,
                     begin+text_size+data_size, begin+text_size+data_size+0x100)
    struct.pack_into('>3I', image, 0x5000, 0x10000, 0x10000+10*0x2000, text_size+data_size)
    image[0x4030:0x4040] = b'overlay\0' + bytes(8)
    struct.pack_into('>6I', image, 0x4040, 1, 2, begin+0x8000, begin+0x8100, begin+0x8200, begin+0x8300)
    struct.pack_into('>6I', image, 0x500c, 0x24000, 0x26000, 256, 0x26000, 0x28000, 256)
    small = struct.pack('>4H', 0, 256, 40, 40) + bytes([0xff])*32 + bytes(range(256))
    image[0x24000:0x24000+len(small)] = small
    image[0x26000:0x26000+len(small)] = small
    payload = bytes((i*17) & 255 for i in range(7000))
    for n in range(10):
        encoded = b''.join(b'\xff'+payload[i:i+8] for i in range(0, len(payload), 8))
        header = struct.pack('>4H', 0, len(payload), 8+875, 8+875)
        # All control bytes precede the contiguous literals in compact MIO.
        chunk = header + bytes([0xff])*875 + payload
        assert len(encoded) == len(chunk)-8
        at = 0x10000+n*0x2000
        image[at:at+len(chunk)] = chunk
    programs = boot_compact_mio.find(image, [(0x1000, 0x2000, 0x80001000)])
    assert programs == ((begin, (payload*10)[:text_size], (payload*10)[text_size:]),
                        (begin+0x8000, bytes(range(256)), bytes(range(256))))
    image[0x1000:0x1008] = bytes(8)
    assert boot_compact_mio.find(image, [(0x1000, 0x2000, 0x80001000)]) is None
