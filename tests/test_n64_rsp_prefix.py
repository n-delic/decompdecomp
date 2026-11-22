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
    assert list(audio_spans(image)) == [(0xd0, 0x10d0)]
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
