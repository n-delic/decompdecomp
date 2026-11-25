"""A function that matches only by luck of a modern compiler, but isn't the game's own code (a 64-bit division helper
filed under a 2009 GCC), is set aside as library code: it leaves the matched count and polishing."""
import json

import pytest

from decompnet.network import NetworkError
from tests.test_claims import _game


def _status(net, name):
    return net.store.one("SELECT status, meta FROM functions WHERE name=?", name)


def test_a_matched_function_is_set_aside_as_library_code_and_given_back(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'matched'), ('g', 10, 'open')])
    with pytest.raises(NetworkError, match='only as library code'):
        net.set_aside_by_you('f', 'game', 'sdk')  # (the plain set-aside is still for open functions)
    net.set_aside_by_you('f', 'game', 'libgcc 64-bit division dispatch', library=True)
    row = _status(net, 'f')
    meta = json.loads(row['meta'])
    assert row['status'] == 'asm' and meta['was_matched'] and 'as library code' in meta['asm_code']
    assert meta['not_c']['you']['why'] == 'libgcc 64-bit division dispatch'
    net.reopen('f', 'game')
    assert _status(net, 'f')['status'] == 'open'


def test_polishing_is_not_handed_a_function_set_aside_as_library_code(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'matched')])
    proj = net._project('game')
    before = net.readability.left(proj)
    net.set_aside_by_you('f', 'game', library=True)
    assert before == 1 and net.readability.left(net._project('game')) == 0


def test_only_a_matched_or_open_function_can_be_set_aside(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'asm')])
    with pytest.raises(NetworkError, match='isn.t open'):
        net.set_aside_by_you('f', 'game', library=True)
