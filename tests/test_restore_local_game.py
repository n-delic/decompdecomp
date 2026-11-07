"""Optional game selection only accepts a console-verified matching local copy."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import saves
from decompnet.network import NetworkError


@pytest.fixture
def local(tmp_path, monkeypatch):
    folder = tmp_path/'checkout';folder.mkdir()
    manifest = {'game':{'sha1':'right', 'file':'original.bin'}}
    def identify(path):
        return SimpleNamespace(read_game=lambda path,name:SimpleNamespace(info={'sha1':path.read_text()}))
    monkeypatch.setattr(saves.consoles, 'identify', identify)
    return SimpleNamespace(project_rows=lambda:[]), folder, manifest


def test_find_adjacent_matching_game_and_ignore_wrong_filename(local):
    net, folder, manifest = local
    (folder/'original.bin').write_text('wrong')
    (folder/'actual.bin').write_text('right')
    assert saves.find_local_game(net, str(folder), folder, manifest, print) == folder/'actual.bin'


def test_find_game_in_combined_checkout_parent(local):
    net, folder, manifest = local
    progress = folder/saves.SAVE_FOLDER;progress.mkdir()
    (folder/'original.bin').write_text('right')
    assert saves.find_local_game(net, str(folder), progress, manifest, print) == folder/'original.bin'


def test_find_already_imported_game_even_for_remote_save(local):
    net, folder, manifest = local
    image = folder/'original.bin';image.write_text('right')
    net.project_rows = lambda:[{'root':str(folder), 'config':json.dumps({'rom':{'sha1':'right','file':image.name}})}]
    assert saves.find_local_game(net, 'https://example.invalid/save.git', folder, manifest, print) == image


def test_wrong_game_never_used_and_remote_clone_not_scanned(local):
    net, folder, manifest = local
    (folder/'original.bin').write_text('wrong')
    with pytest.raises(NetworkError, match='Choose your copy'):
        saves.find_local_game(net, str(folder), folder, manifest, print)
    (folder/'original.bin').write_text('right')
    with pytest.raises(NetworkError, match='Choose your copy'):
        saves.find_local_game(net, 'https://example.invalid/save.git', folder, manifest, print)
