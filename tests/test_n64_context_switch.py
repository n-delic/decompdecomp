"""Synthetic assembly entries that save state before resuming another thread."""

import struct

import pytest

from decompnet import consoles
from decompnet.consoles.n64.context_switch import saves_context


@pytest.mark.parametrize('handoff', [
    (0x17000003, 0, 0x03e00008, 0x27bd00c0, 0xad5d0008),
    (0xad5d0008, 0x3c0a8005, 0x254a0010, 0x8d4a0000, 0x0c0001c0, 0),
])
def test_context_save_entries_accept_the_two_shared_handoffs(handoff):
    names = (*range(4, 8), *range(16, 24), 28, 30, 31)
    gprs = [0xafa00000 | reg << 16 | index * 4 for index, reg in enumerate(names)]
    fprs = [0xe7a00000 | reg << 16 | (0x40 + reg * 4) for reg in range(32)]
    words = (0x27bdff40, *gprs, *fprs, 0xafbf003c, *handoff)
    code = struct.pack(f'>{len(words)}I', *words)
    assert saves_context(code)
    assert 'context switch' in consoles.get('n64').asm_reason(code)
    assert consoles.get('n64').boundary_reason(code) is None
    assert not saves_context(code[:-4])
    changed = bytearray(code)
    struct.pack_into('>I', changed, 4 * (1 + len(gprs)), 0)
    assert not saves_context(changed)
