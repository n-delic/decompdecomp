import struct

import pytest

from decompnet.consoles.n64.rsp_prefix import trim

from .test_n64code import _function


def test_graphics_boot_variant_needs_vector_and_internal_branch_evidence():
    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    image = bytearray(0xd0 + 0x1000)
    image[:36] = bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    assert list(graphics_spans(image)) == []
    for i in range(64):
        struct.pack_into('>I', image, 0x100 + i * 4, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x200 + i * 4, 0x09000400 + i)
    assert list(graphics_spans(image)) == [(0, len(image))]


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


@pytest.mark.parametrize('entry', ['pointers', 'status'])
def test_audio_text_after_bootstrap_requires_vector_program(entry):
    import struct

    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import audio_spans

    image = bytearray(0xd0 + 0x1000)
    image[:len(RSPBOOT)] = RSPBOOT
    image[0xd0:0xe0] = bytes.fromhex('2018036020170f908c3c00308c3b0034' if entry == 'pointers'
                                      else '400558008c3c00308c3b003430a40001')
    for i in range(64):
        struct.pack_into('>I', image, 0x100 + 4 * i, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x200 + 4 * i, 0x09000400 + i)
    assert list(audio_spans(image)) == [(0, 0x10d0)]
    image[0x100:0x200] = bytes(0x100)
    assert not list(audio_spans(image))

@pytest.mark.parametrize('boot', ['standard', 'variant'])
def test_audio_text_before_bootstrap_accepts_pointer_first_entry(boot):
    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import audio_spans

    image = bytearray(0x1000 + 0xd0)
    image[:16] = bytes.fromhex('2018036020170f908c3c00308c3b0034')
    for i in range(64):
        struct.pack_into('>I', image, 0x100 + i * 4, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x200 + i * 4, 0x09000400 + i)
    signature = RSPBOOT if boot == 'standard' else bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    image[0x1000:0x1000+len(signature)] = signature
    assert list(audio_spans(image)) == [(0, 0x1000)]
    image[0x100:0x200] = bytes(0x100)
    assert not list(audio_spans(image))


def test_vector_graphics_overlays_stop_before_cpu_routine():
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    entry = bytes.fromhex('4a00002cc81f201bc81e201c')
    overlay = bytes.fromhex('080007f6201f108c200c4000400b5800')
    image = bytearray(0x2800)
    for start in (0x100, 0x14a0):
        image[start:start+12] = entry
        for i in range(64):
            struct.pack_into('>I', image, start+0x100+i*4, 0x4a000037)
        for i in range(3):
            struct.pack_into('>I', image, start+0x300+i*4, 0x0c000400+i)
        image[start+0x1000:start+0x1010] = overlay
    for i in range(64):
        struct.pack_into('>I', image, 0x1100+0x40+i*4, 0x4a000037)
    cpu = bytes.fromhex('3c0a8001254a11608d4b0000316bff0040086000')
    image[0x2640:0x2640+len(cpu)] = cpu
    image[0x2660:0x2664] = bytes.fromhex('03e00008')
    assert list(graphics_spans(image)) == [(0x100, 0x14a0), (0x14a0, 0x2640)]
    image[0x2640:0x2644] = bytes(4)
    assert list(graphics_spans(image)) == [(0x100, 0x14a0)]


def test_vector_overlay_before_called_cpu_frame():
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    image = bytearray(0x1600)
    start = 0x100
    image[start:start + 12] = bytes.fromhex('4a00002cc81f201bc81e201c')
    for i in range(64):
        struct.pack_into('>I', image, start + 0x100 + i * 4, 0x4a000037)
    for i in range(3):
        struct.pack_into('>I', image, start + 0x300 + i * 4, 0x0c000400 + i)
    image[0x1100:0x1110] = bytes.fromhex('080007f6201f108c200c4000400b5800')
    image[0x1290:0x12a8] = bytes.fromhex('080004848c1800cc000000000000000027bdffc0afbf003c')
    image[0x12c0:0x12c8] = bytes.fromhex('03e0000827bd0040')
    assert list(graphics_spans(image)) == [(start, 0x12a0)]
    image[0x12a4:0x12a8] = bytes(4)
    assert not list(graphics_spans(image))


def test_standard_boot_and_jump_to_vector_graphics():
    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    image = bytearray(0xd0 + 0x1000)
    image[:len(RSPBOOT)] = RSPBOOT
    struct.pack_into('>I', image, 0xd0, 0x090005e8)
    for i in range(64):
        struct.pack_into('>I', image, 0x100 + 4*i, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x200 + 4*i, 0x09000400 + i)
    assert list(graphics_spans(image)) == [(0, len(image))]
    image[0x100:0x200] = bytes(0x100)
    assert not list(graphics_spans(image))


def test_bootstrapped_vector_overlay_stops_before_a_cpu_frame():
    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    image = bytearray(0x1600)
    image[:len(RSPBOOT)] = RSPBOOT
    code = 0xd0
    image[code:code+12] = bytes.fromhex('4a00002cc81f201bc81e201c')
    for i in range(64):
        struct.pack_into('>I', image, code+0x100+i*4, 0x4a000037)
        struct.pack_into('>I', image, code+0x1100+i*4, 0x4a000037)
    for i in range(3):
        struct.pack_into('>I', image, code+0x300+i*4, 0x0c000400+i)
    struct.pack_into('>I', image, code+0xffc, 0x080007f6)
    stop = code+0x1420
    struct.pack_into('>2I', image, stop-24, 0x08000513, 0x4a952748)
    struct.pack_into('>5I', image, stop, 0x27bdffe8, 0xafbf0010, 0x8fbf0010, 0x03e00008, 0x27bd0018)
    assert (0, stop) in list(graphics_spans(image))
    image[stop-20:stop-16] = bytes(4)  # no vector delay-slot evidence
    assert (0, stop) not in list(graphics_spans(image))


def test_graphics_boot_marker_after_return_is_not_cpu_code():
    from decompnet.consoles.n64.port import RSPBOOT
    from decompnet.consoles.n64.rsp_prefix import graphics_spans

    image = bytearray(0x20 + 0xd0 + 0x1000)
    image[0x14:0x20] = bytes.fromhex('03e00008000000006e736967')
    image[0x20:0x44] = bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    for i in range(64):
        struct.pack_into('>I', image, 0x120 + 4*i, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, 0x220 + 4*i, 0x09000400+i)
    assert list(graphics_spans(image)) == [(0x1c, len(image))]
    image[0x14:0x18] = bytes(4)
    assert list(graphics_spans(image)) == [(0x20, len(image))]
    image[0x120:0x220] = bytes(0x100)
    assert not list(graphics_spans(image))
