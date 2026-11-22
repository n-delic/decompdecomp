import struct
import zlib

from decompnet.consoles.n64 import boot_gzip, boot_raw_deflate, code


def test_loader_header_binds_bounded_raw_stream_and_indirect_entry(monkeypatch):
    header, source, scratch, target = 0x10000, 0x10010, 0x80020000, 0x80400000
    text = bytes.fromhex('27bdffe0') * 8 + bytes.fromhex('03e0000800000000')
    data = b'data' * 8
    compress = zlib.compressobj(wbits=-15)
    packed = compress.compress(text+data) + compress.flush()
    rom = bytearray(source + len(packed))
    struct.pack_into('>4I', rom, header, target, target, target+len(text)+len(data), target+len(text)+len(data)+16)
    rom[source:] = packed
    # The decoded entry pointer is loaded into v0 and used by jalr.
    struct.pack_into('>I', rom, 0x1080, 0x8c820004)
    struct.pack_into('>I', rom, 0x1084, 0x0040f809)
    constant = lambda value: dict(kind='constant', value=value)
    dma = dict(kind='call', instruction=0x80000420, target=0x80000500, block=1,
               args=[constant(header), constant(scratch), constant(16)])
    load_output = dict(kind='load', instruction=0x80000440, block=1, width=4, place=constant(scratch))
    inflate = dict(kind='call', instruction=0x80000448, target=0x80000600, block=1,
                   args=[constant(source), dict(kind='memory', value=0x80000440), constant(len(packed))])
    load_entry = dict(kind='load', instruction=0x80000480, block=1, width=4, place=constant(scratch+4))
    jump = dict(kind='call', instruction=0x80000484, target=None, block=1, args=[])
    monkeypatch.setattr(boot_raw_deflate, 'read', lambda *_: dict(events=[dma, load_output, inflate, load_entry, jump]))
    monkeypatch.setattr(code, '_looks_like_code', lambda value: value == text+data)
    ranges = [(0x1000, 0x1100, 0x80000400)]
    assert boot_raw_deflate.find(bytes(rom), ranges) == ((target, text, data),)
    monkeypatch.setattr(boot_gzip, 'read', lambda *_: dict(events=[]))
    assert boot_gzip.verify_analysis(bytes(rom), bytes(rom) + text + data, ranges)
    assert not boot_gzip.verify_analysis(bytes(rom), bytes(rom) + text + data[:-1] + b'?', ranges)
    broken = bytearray(rom)
    struct.pack_into('>I', broken, 0x1084, 0x0060f809)
    assert boot_raw_deflate.find(bytes(broken), ranges) is None
    struct.pack_into('>I', broken, 0x1084, 0x0040f809)
    struct.pack_into('>I', broken, header+8, target+len(text)+len(data)+4)
    assert boot_raw_deflate.find(bytes(broken), ranges) is None


def test_raw_loader_rejects_unrelated_headers_and_oversized_streams(monkeypatch):
    constant = lambda value: dict(kind='constant', value=value)
    dma = dict(kind='call', instruction=0x80000420, target=0x80000500, block=1,
               args=[constant(0x10000), constant(0x80020000), constant(16)])
    monkeypatch.setattr(boot_raw_deflate, 'read', lambda *_: dict(events=[dma]))
    rom = bytearray(0x10020)
    struct.pack_into('>4I', rom, 0x10000, 0x80400000, 0x80400000, 0x80900000, 0x80900000)
    assert boot_raw_deflate.find(bytes(rom), [(0x1000, 0x1100, 0x80000400)]) is None
