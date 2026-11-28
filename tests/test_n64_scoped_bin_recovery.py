"""A call into one overlay's raw gap cannot name another overlay's bytes."""

import pytest

from decompnet.consoles.n64.code import _called_scoped_bin_bodies


@pytest.mark.parametrize('caller_bank', [0, 1])
def test_recover_only_the_callers_declared_bank(caller_bank):
    image = bytearray(0x200)
    vram = 0x80001000
    target = vram + 0x40
    call = (0x0c000000 | ((target & 0x0fffffff) >> 2)).to_bytes(4, 'big')
    leaf = bytes.fromhex('24020001 03e00008 00000000')
    for index in (0, 1):
        start = 0x100 + index * 0x80
        image[start:start + 16] = (call if index == caller_bank else bytes(4)) + bytes.fromhex(
            '00000000 03e00008 00000000')
        image[start + 0x40:start + 0x4c] = leaf
    cfg = {'segments': [
        {'name': 'a', 'type': 'code', 'start': 0x100, 'vram': vram, 'exclusive_ram_id': 'slot',
         'subsegments': [[0x100, 'c', 'a'], [0x120, 'bin', 'a_gap']]},
        {'name': 'b', 'type': 'code', 'start': 0x180, 'vram': vram, 'exclusive_ram_id': 'slot',
         'subsegments': [[0x180, 'c', 'b'], [0x1a0, 'bin', 'b_gap']]}, [0x200]]}
    known = [(0x100, 0x110, vram), (0x180, 0x190, vram)]
    start = 0x140 + caller_bank * 0x80
    assert _called_scoped_bin_bodies(cfg, bytes(image), known, lambda _: None) == [(start, start + 12, target)]
    assert not _called_scoped_bin_bodies(cfg, bytes(image), known + [(start, start + 12, target)], lambda _: None)
    cfg['segments'][caller_bank]['subsegments'][1][1] = 'data'
    assert not _called_scoped_bin_bodies(cfg, bytes(image), known, lambda _: None)
    cfg['segments'][caller_bank]['subsegments'][1][1] = 'bin'
    cfg['segments'][caller_bank].pop('exclusive_ram_id')
    assert not _called_scoped_bin_bodies(cfg, bytes(image), known, lambda _: None)
