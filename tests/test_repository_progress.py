"""Autosaved progress commits use the ordinary progress engine and a local bare Git remote."""
import json
import shutil
import subprocess

import pytest

from decompnet.app import repository_git, repository_progress, repository_sync, saves
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


@pytest.fixture(params=['n64', 'ps1', 'ps2', 'psp'])
def progress(tmp_path, request):
    if not shutil.which('git'):pytest.skip('needs git')
    remote = tmp_path/'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    root = tmp_path/'checkout'
    subprocess.run(['git', 'clone', '-q', str(remote), str(root)], check=True)
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': request.param, 'rom': {'sha1': 'homemade'},
           'instructions': 'Keep these notes'}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('game', 'Homemade game', str(tmp_path/'game'), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'typedef int s32;',0,'')", (pid,))
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,best_source,best_score) "
                   "VALUES(?,'f',4096,4,1,'open','','void f(void) {}',10)", (pid,))
    net.progress.sync('game', force=True)
    shutil.copytree(saves.default_folder(net, 'game'), root, dirs_exist_ok=True)
    net._update_cfg('game', lambda cfg: cfg.update(progress_folder=str(root)))
    net.progress.sync('game', force=True)
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial progress')
    git(root, 'push', '-u', 'origin', 'HEAD')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='void f(void) { return; }',best_score=0 WHERE project_id=?", (pid,))
    net.progress.sync('game', force=True)
    return net, root, remote


def test_autosaved_matches_promotions_and_metadata_can_be_committed(progress):
    net, root, remote = progress
    assert not (root/'best/f.c').exists() and (root/'src/f.c').exists()
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed'] and result['mode'] == 'progress'
    assert result['matched'] == 1 and result['best'] == 0
    assert git(remote, 'rev-parse', 'HEAD') == result['commit']
    assert git(root, 'status', '--porcelain') == ''
    assert json.loads(git(root, 'show', 'HEAD:decompdecomp.json'))['instructions'] == 'Keep these notes'
    assert 'best/f.c' not in git(root, 'ls-files')
    repeated = repository_sync.run(net, 'game', root, commit_push=True)
    assert repeated['commit'] == result['commit']


def test_review_progress_needs_no_upstream_or_build(progress):
    net, root, _ = progress
    git(root, 'branch', '--unset-upstream')
    before = git(root, 'rev-parse', 'HEAD')
    assert repository_sync.status(net, 'game')['mode'] == 'progress'
    assert repository_sync.run(net, 'game', root)['matched'] == 1
    assert git(root, 'rev-parse', 'HEAD') == before


def test_unrelated_and_staged_edits_are_protected(progress):
    net, root, _ = progress
    (root/'private.rom').write_bytes(b'private homemade input')
    with pytest.raises(NetworkError, match='existing changes'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'
    (root/'private.rom').unlink()
    git(root, 'add', 'README.md')
    before = git(root, 'diff', '--cached')
    with pytest.raises(NetworkError, match='staged changes'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'diff', '--cached') == before


def test_helper_writes_after_capture_remain_uncommitted(progress, monkeypatch):
    net, root, _ = progress
    original = repository_progress.commit_snapshot
    def advance(root, files, paths, log, **kwargs):
        (root/'src/f.c').write_text('new helper work\n')
        return original(root, files, paths, log, **kwargs)
    monkeypatch.setattr(repository_progress, 'commit_snapshot', advance)
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed']
    assert git(root, 'show', 'HEAD:src/f.c') == 'void f(void) { return; }'
    assert (root/'src/f.c').read_text() == 'new helper work\n'
    assert git(root, 'status', '--porcelain') == 'M src/f.c'


def test_an_editor_change_during_capture_is_not_committed(progress, monkeypatch):
    net, root, _ = progress
    original = saves._read_owned
    def edited(folder):
        (root/'src/f.c').write_text('unjudged editor change\n')
        return original(folder)
    monkeypatch.setattr(saves, '_read_owned', edited)
    with pytest.raises(NetworkError, match='changed while capturing'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'


def test_index_lock_and_branch_change_preserve_checkpoint(progress, monkeypatch):
    _, root, _ = progress
    files = {'src/f.c': b'captured\n'}
    lock = root/'.git/index.lock'
    lock.write_text('another Git process')
    with pytest.raises(NetworkError, match='Another Git operation'):
        repository_progress.commit_snapshot(root, files, set(files), print)
    assert lock.read_text() == 'another Git process'
    lock.unlink()
    original = repository_git.git
    def staged(root, *args, **kwargs):
        if args[0] == 'write-tree':
            git(root, 'add', 'src/f.c')
        return original(root, *args, **kwargs)
    monkeypatch.setattr(repository_git, 'git', staged)
    with pytest.raises(NetworkError, match='staged files changed'):
        repository_progress.commit_snapshot(root, files, set(files), print)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'
    assert git(root, 'diff', '--cached', '--name-only') == 'src/f.c'
    assert not lock.exists()


def test_nested_progress_excludes_other_repository_files(progress):
    net, root, _ = progress
    repository_sync.run(net, 'game', root, commit_push=True)
    folder = root/'progress'
    folder.mkdir()
    for name in ('README.md', 'decompdecomp.json', 'decompdecomp-progress.svg', 'src', 'best', 'include'):
        if (root/name).exists():shutil.move(root/name, folder/name)
    (root/'README.md').write_text('Repository guide\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'nest progress')
    cfg = json.loads(net.store.one("SELECT config FROM projects WHERE slug='game'")['config'])
    cfg['progress_folder'] = str(folder)
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?,name='Renamed homemade project' WHERE slug='game'", (json.dumps(cfg),))
    result = repository_sync.run(net, 'game', root, commit_push=True)
    assert result['pushed']
    assert git(root, 'show', 'HEAD:README.md') == 'Repository guide'
    assert json.loads(git(root, 'show', 'HEAD:progress/decompdecomp.json'))['title'] == 'Renamed homemade project'


def test_failed_progress_push_can_be_retried(progress):
    net, root, remote = progress
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    failed = repository_sync.run(net, 'game', root, commit_push=True)
    assert not failed['pushed'] and failed['push_error']
    assert git(root, 'rev-parse', 'HEAD') == failed['commit']
    hook.unlink()
    retried = repository_sync.run(net, 'game', root, commit_push=True)
    assert retried['pushed'] and retried['commit'] == failed['commit']


def test_symbolic_link_and_conflicts_are_not_committed(progress, tmp_path):
    net, root, _ = progress
    outside = tmp_path/'outside'
    outside.write_text('private content')
    path = root/'src/f.c'
    path.unlink()
    try:path.symlink_to(outside)
    except OSError:pytest.skip('needs symlinks')
    with pytest.raises(NetworkError, match='symbolic'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert outside.read_text() == 'private content'
    path.unlink()
    path.write_text('<<<<<<< HEAD\nconflict\n=======\nother\n>>>>>>> branch\n')
    with pytest.raises(NetworkError, match='conflicts'):
        repository_sync.run(net, 'game', root, commit_push=True)
    assert git(root, 'log', '-1', '--format=%s') == 'initial progress'


def test_snapshot_preserves_line_endings_and_rejects_a_new_head(progress):
    _, root, _ = progress
    head = git(root, 'rev-parse', 'HEAD')
    payload = b'void f(void) { return; }\r\n'
    commit = repository_progress.commit_snapshot(root, {'src/f.c': payload}, {'src/f.c'}, print, expected_head=head)
    assert subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:src/f.c']) == payload
    assert commit != head
    with pytest.raises(NetworkError, match='committed new work'):
        repository_progress.commit_snapshot(root, {'src/f.c': b'old'}, {'src/f.c'}, print, expected_head=head)
    assert git(root, 'rev-parse', 'HEAD') == commit
