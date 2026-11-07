"""Git setup is local, scoped to the remembered save, and never stages game data."""
import shutil

import pytest

from decompnet.app import match_commits, repository_progress, repository_setup, saves
from decompnet.network import NetworkError

from . import test_progress

base_project = test_progress.project


@pytest.fixture(params=["n64", "ps1", "ps2", "psp"])
def project(base_project, request):
    net, folder, judged = base_project
    net._update_cfg("game", lambda cfg: cfg.update(platform=request.param))
    net.progress.sync("game", force=True)
    return net, folder, judged
from .test_repository_progress import git


def test_init_checkpoint_and_origin_allow_automatic_commits(project, tmp_path):
    net, folder, _ = project
    (folder/'private.rom').write_bytes(b'private game')
    (folder/'notes.txt').write_text('unrelated edits')
    (folder/'.gitignore').write_text('# my existing rules\n/src/private.c\n')
    assert not repository_setup.status(net, 'game')['repository']
    state = repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert state['repository'] and not state['checkpoint'] and not state['origin']
    assert git(folder, 'ls-files') == ''
    assert (folder/'.gitignore').read_text().endswith('# my existing rules\n/src/private.c\n')
    assert git(folder, 'check-ignore', 'private.rom') == 'private.rom'
    assert git(folder, 'check-ignore', 'src/private.c') == 'src/private.c'
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    state = repository_setup.run(net, 'game', 'checkpoint', '', lambda line: None)
    assert state['checkpoint']
    tracked = git(folder, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()
    assert saves.MANIFEST in tracked and '.gitignore' in tracked
    assert 'private.rom' not in tracked and 'notes.txt' not in tracked
    assert match_commits.configure(net, 'game', True, str(folder))['enabled']
    remote = tmp_path/'origin.git'
    git(tmp_path, 'init', '--bare', str(remote))
    state = repository_setup.run(net, 'game', 'origin', str(remote), lambda line: None)
    assert state['origin']
    assert git(folder, 'remote', 'get-url', 'origin') == str(remote)
    with pytest.raises(NetworkError, match='already configured'):
        repository_setup.run(net, 'game', 'origin', 'another', lambda line: None)


def test_staged_changes_and_links_are_preserved(project):
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    (folder/'src').mkdir(exist_ok=True)
    (folder/'src/personal.c').write_text('private')
    git(folder, 'add', 'src/personal.c')
    staged = git(folder, 'diff', '--cached', '--name-only')
    with pytest.raises(NetworkError, match='staged'):
        repository_setup.run(net, 'game', 'checkpoint', '', lambda line: None)
    assert git(folder, 'diff', '--cached', '--name-only') == staged


def test_parent_repository_is_detected_without_nested_init(project):
    net, folder, _ = project
    git(folder.parent, 'init')
    assert repository_setup.status(net, 'game')['root'] == str(folder.parent)
    with pytest.raises(NetworkError, match='already belongs'):
        repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert not (folder/'.git').exists()


def test_remembered_save_can_become_live_progress(project, tmp_path):
    net, folder, _ = project
    target = tmp_path/'backup'
    saves.save(net, 'game', target)
    state = repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert state['linked'] and saves.default_folder(net, 'game') == target
    assert folder.exists()
    assert (target/'.git').is_dir()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new draft',best_score=10 WHERE name='f'")
    net.progress.sync('game', force=True)
    assert (target/'best/f.c').read_text() == 'new draft\n'
    assert not (folder/'best/f.c').exists()


def test_mismatched_save_is_not_adopted(project, tmp_path):
    net, folder, _ = project
    target = tmp_path/'backup'
    shutil.copytree(folder, target)
    net._update_cfg('game', lambda cfg: cfg.update(save_folder=str(target)))
    (target/'include/common.h').write_text('changed header')
    with pytest.raises(NetworkError, match='differs'):
        repository_setup.run(net, 'game', 'init', '', lambda line: None)
    assert saves.default_folder(net, 'game') == folder
    assert not (target/'.git').exists()


def test_origin_on_imported_project_uses_its_source_checkout(project, tmp_path):
    net, folder, _ = project
    root = tmp_path/'source'
    root.mkdir()
    git(root, 'init')
    with net.store.tx() as db:
        db.execute("UPDATE projects SET root=? WHERE slug='game'", (str(root),))
    net._update_cfg('game', lambda cfg: cfg.update(kind='fork', save_folder=str(tmp_path/'backup')))
    state = repository_setup.status(net, 'game')
    assert not state['portable'] and state['root'] == str(root)
    assert state['folder'] == str(root) and state['save_folder'] == str(tmp_path/'backup')
    assert repository_setup.run(net, 'game', 'origin', 'git@example.invalid:project.git', lambda line: None)['origin']
    assert git(root, 'remote', 'get-url', 'origin') == 'git@example.invalid:project.git'


def test_first_checkpoint_rejects_a_concurrent_initial_commit(project):
    net, folder, _ = project
    repository_setup.run(net, 'game', 'init', '', lambda line: None)
    git(folder, 'config', 'user.name', 'Test')
    git(folder, 'config', 'user.email', 'test@example.invalid')
    git(folder, 'add', '.gitignore')
    git(folder, 'commit', '-qm', 'Other Git operation')
    head = git(folder, 'rev-parse', 'HEAD')
    with pytest.raises(NetworkError, match='committed new work'):
        repository_progress.commit_snapshot(folder, {}, set(), lambda line: None, expected_head=None)
    assert git(folder, 'rev-parse', 'HEAD') == head


def test_select_build_root_saves_progress_and_enables_existing_repository(project, tmp_path):
    from decompnet.app import repository_sync

    net, old, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    git(root, 'init', '--initial-branch=main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build tools')
    (root / 'src').mkdir()
    (root / 'src' / 'handwritten.c').write_text('int preserved;')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'build checkout')
    (root / 'src' / 'handwritten.c').write_text('int newer_hand_written;')
    result = saves.save(net, 'game', root)
    assert result['folder'] == str(root / 'progress')
    assert (root / 'progress' / saves.MANIFEST).is_file()
    assert not (root / saves.MANIFEST).exists()
    assert (root / 'src' / 'handwritten.c').read_text() == 'int newer_hand_written;'
    state = repository_setup.status(net, 'game')
    assert state['root'] == str(root) and state['build_folder'] == str(root)
    assert not state['linked']
    repository_setup.run(net, 'game', 'checkpoint', '', lambda _: None)
    assert saves.default_folder(net, 'game') == root / 'progress'
    assert match_commits.configure(net, 'game', True, str(root))['enabled']
    assert not repository_sync.progress_checkout(net, 'game', root), 'build sync must still use the build route'
    assert repository_progress.linked_checkout(net, 'game', root)
    assert git(root, 'show', 'HEAD:src/handwritten.c') == 'int preserved;'
    assert 'progress/decompdecomp.json' in git(root, 'ls-files')
    assert not (root / 'progress' / '.git').exists()
    assert (root / 'src' / 'handwritten.c').read_text() == 'int newer_hand_written;'
    assert 'src/handwritten.c' in git(root, 'diff', '--name-only')


def test_detected_progress_does_not_follow_a_child_link(project, tmp_path):
    net, _, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'progress').symlink_to(outside, target_is_directory=True)
    with pytest.raises(NetworkError, match='symbolic link'):
        saves.save(net, 'game', root)
    assert list(outside.iterdir()) == []


def test_detected_progress_rejects_a_different_game(project, tmp_path):
    import json

    net, _, _ = project
    root = tmp_path / 'build-checkout'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    (root / 'progress').mkdir()
    manifest = root / 'progress' / saves.MANIFEST
    text = json.dumps({'game': {'sha1': 'another-game'}})
    manifest.write_text(text)
    with pytest.raises(NetworkError, match='another game'):
        saves.save(net, 'game', root)
    assert manifest.read_text() == text


def test_remembered_separate_build_checkout_stays_available(project, tmp_path):
    net, _, _ = project
    root = tmp_path / 'separate-build'
    root.mkdir()
    (root / 'build.json').write_text('{}')
    (root / 'build.py').write_text('# build')
    net._update_cfg('game', lambda cfg: cfg.update(repository_sync_folder=str(root)))
    assert repository_setup.status(net, 'game')['build_folder'] == str(root)
