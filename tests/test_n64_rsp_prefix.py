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
