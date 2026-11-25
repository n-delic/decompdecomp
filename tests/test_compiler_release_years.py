"""A game never uses a compiler that came out after it (its `released` setting): twine had a 6-instruction function
filed under a 2009 GCC that no N64 game was built with, and the Linux VM that compiler needs held up its polishing."""
import json

import pytest

from decompnet import consoles
from decompnet.network import NetworkError
from tests.test_claims import _game

N64 = {'platform': 'n64', 'kind': 'rom'}


def _ids(cfg):
    return {c.id for c in consoles.of(cfg).compilers_for(cfg)}


def test_a_game_without_a_year_may_use_every_compiler():
    assert 'gcc-4.4.0-n64' in _ids(N64) and consoles.too_new(N64, 'gcc-4.4.0-n64') == ''


def test_compilers_newer_than_the_game_are_left_out():
    cfg = {**N64, 'released': 1998}
    ids = _ids(cfg)
    assert {'gcc-4.4.0-n64', 'mipspro-7.4.4', 'gcc-egcs-2.91.66'}.isdisjoint(ids)
    assert {'gcc-2.7.2-kmc', 'ido-7.1', 'sn-n64-281'} <= ids
    assert '2009' in consoles.too_new(cfg, 'gcc-4.4.0-n64') and consoles.too_new(cfg, 'ido-7.1') == ''
    assert [c.id for c in consoles.of(cfg).project_compilers(None, cfg)] == [c.id for c in consoles.of(cfg).compilers_for(cfg)]


@pytest.mark.parametrize('bad', ['soon', 1800, 3000])
def test_the_release_year_must_be_a_year(tmp_path, bad):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    with pytest.raises(NetworkError, match='release year'):
        net.set_released('game', bad)


def test_the_year_is_kept_and_refuses_a_newer_compiler_by_name(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps(N64),))
    net.set_released('game', 1998)
    cfg = net.cfg(net._project('game'))
    assert cfg['released'] == 1998
    with pytest.raises(NetworkError, match='came out in 2009'):
        net._toolchain_named(cfg, 'gcc-4.4.0-n64')
    assert net._toolchain_named(cfg, 'ido-7.1') == 'ido-7.1'
    net.set_released('game', '')
    assert 'released' not in net.cfg(net._project('game'))
    assert net._toolchain_named(net.cfg(net._project('game')), 'gcc-4.4.0-n64') == 'gcc-4.4.0-n64'
