"""Local bare remotes exercise commit ownership, retry and upstream safety without credentials."""
import subprocess

import pytest

from decompnet.app import repository_git
from decompnet.network import NetworkError


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


@pytest.fixture
def checkout(tmp_path):
    remote = tmp_path/'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    root = tmp_path/'checkout'
    subprocess.run(['git', 'clone', '-q', str(remote), str(root)], check=True)
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root/'README.md').write_text('original')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial')
    git(root, 'push', '-u', 'origin', 'HEAD')
    return root, remote


def test_commit_push_only_generated_paths_and_retry(checkout):
    root, remote = checkout
    upstream = repository_git.prepare(root, lambda text:None)
    (root/'progress').mkdir()
    (root/'progress/notes.txt').write_text('all notes')
    (root/'private.rom').write_text('never commit')
    result = repository_git.finish(root, {'progress/notes.txt'}, upstream, lambda text:None)
    assert result['pushed']
    assert git(remote, 'rev-parse', 'HEAD') == result['commit']
    assert 'private.rom' not in git(root, 'ls-files')
    # Same clean inputs retry a push without manufacturing another commit.
    repeated = repository_git.finish(root, {'progress/notes.txt'}, upstream, lambda text:None)
    assert repeated['commit'] == result['commit']


def test_push_failure_retains_commit_and_retry(checkout):
    root, remote = checkout
    upstream = repository_git.prepare(root, print)
    # A server hook denies the push after the preflight succeeded.
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
    (root/'README.md').write_text('new progress')
    result = repository_git.finish(root, {'README.md'}, upstream, print)
    assert not result['pushed'] and result['push_error']
    assert git(root, 'rev-parse', 'HEAD') == result['commit']
    assert not git(root, 'status', '--porcelain')
    hook.unlink()
    assert repository_git.finish(root, {'README.md'}, upstream, print)['pushed']


def test_no_upstream_or_detached_head(checkout):
    root, _ = checkout
    git(root, 'branch', '--unset-upstream')
    with pytest.raises(NetworkError):repository_git.prepare(root, print)
    git(root, 'checkout', '--detach')
    with pytest.raises(NetworkError):repository_git.prepare(root, print)


def test_remote_ahead_rejected(checkout, tmp_path):
    root, remote = checkout
    other = tmp_path/'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    (other/'new.txt').write_text('remote change')
    git(other, 'add', '.')
    git(other, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'remote')
    git(other, 'push')
    with pytest.raises(NetworkError):repository_git.prepare(root, print)


def test_other_staged_changes_not_committed(checkout):
    root, _ = checkout
    upstream = repository_git.prepare(root, print)
    (root/'README.md').write_text('unrelated')
    git(root, 'add', 'README.md')
    with pytest.raises(NetworkError):repository_git.finish(root, set(), upstream, print)
