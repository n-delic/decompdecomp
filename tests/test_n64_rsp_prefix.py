import struct

from decompnet.consoles.n64.rsp_prefix import trim

from .test_n64code import _function


def test_trim_inferred_rsp_prefix_preserves_called_cpu_leaf():
    base = 0x80001000
    vectors = [0x4a000037]*64
    leaf = [0x00801021, 0x03e00008, 0]
    frame = base+4*(len(vectors)+len(leaf))
    start = base+4*len(vectors)
    words = vectors+leaf+_function(frame, start)+_function(frame+32, frame)
    image = struct.pack(f'>{len(words)}I', *words)
    ranges = [(0, len(image), base)]
    assert trim(image, ranges, []) == [(256, len(image), start)]
    blocked = words+_function(frame+64, base+4)
    image = struct.pack(f'>{len(blocked)}I', *blocked)
    ranges = [(0, len(image), base)]
    assert trim(image, ranges, []) == ranges


def test_audio_text_after_bootstrap_requires_vector_program():
    import struct

    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import audio_spans

    image = bytearray(0xd0 + 0x1000)
    image[:len(RSPBOOT)] = RSPBOOT
    image[0xd0:0xe0] = bytes.fromhex('2018036020170f908c3c00308c3b0034')
    for i in range(64):
        struct.pack_into('>I', image, 0x100 + 4 * i, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x200 + 4 * i, 0x09000400 + i)
    assert list(audio_spans(image)) == [(0xd0, 0x10d0)]
    image[0x100:0x200] = bytes(0x100)
    assert not list(audio_spans(image))
