"""Function names that differ only in capitals share one saved file on macOS and Windows disks.

An N64 game had selectStateReset and SelectStateReset: every sync found the other function's file where it meant to
write, rolled back and tried again, so its saves and automatic commits never finished and every Commit saved
progress said its progress changed while capturing it."""
import pytest

from decompnet.app import progress as progress_module
from decompnet.app import saves
from decompnet.network import NetworkError

from . import test_match_commits

automatic = test_match_commits.automatic
progress = test_match_commits.progress


@pytest.fixture
def named(automatic, monkeypatch):
    net, root, remote, agent = automatic
    pid = net.project_row('game')['id']
    with net.store.tx() as db:
        for name, vram in (('f', 4096), ('g', 4100)):
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,'func')", (pid, name, vram, 4))
    monkeypatch.setattr(net, 'reader', lambda *a: lambda addr, size: None)
    return net, root, remote, agent


def names(net):
    return {row['name'] for row in net.store.all("SELECT name FROM functions")}


def test_rename_refuses_a_name_that_differs_only_in_capitals(named):
    net, _, _, agent = named
    with pytest.raises(NetworkError, match='`F` differs from `f` only in capitals'):
        net.rename_symbol(agent, 'game', 'g', 'F')
    assert names(net) == {'f', 'g'}


def test_a_batch_cannot_create_a_capitals_clash_either(named):
    net, _, _, agent = named
    with pytest.raises(NetworkError, match='only in capitals'):
        net.rename_symbols(agent, 'game', [('f', 'resetState'), ('g', 'ResetState')])
    assert names(net) == {'f', 'g'}


def test_a_function_may_change_the_capitals_of_its_own_name(named):
    net, _, _, agent = named
    net.rename_symbol(agent, 'game', 'g', 'G')
    assert names(net) == {'f', 'G'}


def test_saving_waits_and_says_which_functions_clash(named, monkeypatch):
    net, root, _, agent = named
    store = net.project_store('game')
    pid = store.one("SELECT id FROM projects WHERE slug='game'")['id']
    monkeypatch.setattr(progress_module, '_ignores_case', lambda folder: True)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='F' WHERE name='g'")
        db.execute("UPDATE symbols SET name='F' WHERE name='g'")
    assert net.progress.sync('game', force=True) is False
    message = net.progress.errors['game']
    assert 'Functions `F` and `f` differ only in capitals' in message and 'rename one of each pair with rename_symbol' in message
    assert 'function page' not in message  # (the function page has no rename control)
    assert net.project_row('game')['id'] == pid  # (the project still opens, so the rename below can run)
    net.rename_symbol(agent, 'game', 'F', 'reset_state')
    net.progress.sync('game', force=True)
    assert 'game' not in net.progress.errors
    monkeypatch.setattr(progress_module, '_ignores_case', lambda folder: False)
    assert progress_module.case_clash(store, pid, saves.default_folder(net, 'game')) is None


def test_case_detection_reads_the_disk(tmp_path):
    (tmp_path/'src').mkdir()
    other = tmp_path/'SRC'
    assert progress_module._ignores_case(tmp_path) == other.exists()


def test_a_leftover_file_spelled_with_other_capitals_moves_to_unlisted(automatic, monkeypatch):
    """After the rename, the shared file kept the former name's spelling and still hid the other function's C."""
    net, _, _, _ = automatic
    folder = saves.default_folder(net, 'game')
    pid = net.project_store('game').one("SELECT id FROM projects WHERE slug='game'")['id']
    for name in ('src/f.c', 'best/f.c'):
        (folder/name).unlink(missing_ok=True)
    (folder/'best/F.c').write_text('void f(void) { /* former spelling */ }\n')
    disk = progress_module._read(folder)
    assert 'best/F.c' in disk
    monkeypatch.setattr(progress_module, '_ignores_case', lambda folder: True)
    lines = []
    repaired = progress_module._repair(net, pid, folder, disk, lines.append)
    assert 'best/F.c' not in repaired
    assert (folder/'unlisted/best/F.c').read_text() == 'void f(void) { /* former spelling */ }\n'
    assert any('moved to unlisted/' in line for line in lines)
