import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import collab
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_existing_rom_copy_resolves_without_external_original(tmp_path):
    net, _ = _game(tmp_path, [('f', 4, 'open')])
    root = tmp_path / 'stored'
    root.mkdir()
    game = root / 'game.z64'
    game.write_bytes(b'own copy')
    with net.store.tx() as db:
        db.execute('UPDATE projects SET root=?,config=? WHERE slug=?',
                   (str(root), json.dumps({'kind':'rom','rom':{'file':'game.z64'}}), 'game'))
    assert collab.project_game(net, 'game') == game
    game.unlink()
    with pytest.raises(NetworkError, match='original game file'):
        collab.project_game(net, 'game')


def test_source_project_uses_declared_copy_or_verified_extracted_program(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f',4,'open')])
    proj = net.project_row('game')
    root = Path(proj['root'])
    root.mkdir(parents=True, exist_ok=True)
    directory = root / 'orig'
    directory.mkdir()
    game = directory / 'disc.iso'
    game.write_bytes(b'own disc')
    monkeypatch.setattr(net,'fork_slug',lambda _: 'game')
    fp = SimpleNamespace(needs=lambda _: {'path':'orig/disc.iso'})
    monkeypatch.setattr(net.forks,'get',lambda _: (proj,fp,{}))
    assert collab.project_game(net,'game') == game
    fp.needs = lambda _: {'path':'orig/'}
    net._update_cfg('game',lambda cfg:cfg.__setitem__('game_sha1','expected'))
    monkeypatch.setattr(collab.forkroom,'game_identity',lambda _: 'expected')
    assert collab.project_game(net,'game') == game
    monkeypatch.setattr(collab.forkroom,'game_identity',lambda _: 'different')
    with pytest.raises(NetworkError,match='original game file'):
        collab.project_game(net,'game')
