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


def test_detached_head_has_actionable_error(checkout):
    root, _ = checkout
    git(root, 'checkout', '--detach')
    with pytest.raises(NetworkError,match='detached HEAD'):repository_git.prepare(root, print)


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


def test_missing_upstream_reuses_same_branch_on_only_remote(checkout):
    root,remote=checkout
    branch=git(root,'branch','--show-current')
    git(root,'branch','--unset-upstream')
    lines=[]
    upstream=repository_git.prepare(root,lines.append)
    assert upstream==('origin','refs/heads/'+branch)
    (root/'README.md').write_text('saved progress')
    result=repository_git.finish(root,{'README.md'},upstream,lines.append)
    assert result['pushed']
    assert git(remote,'rev-parse','refs/heads/'+branch)==result['commit']
    assert git(root,'rev-parse','--abbrev-ref','@{upstream}')=='origin/'+branch
    assert any('No upstream' in line for line in lines)


def test_new_branch_without_upstream_is_published_without_changing_default(checkout):
    root,remote=checkout
    original=git(remote,'rev-parse','HEAD')
    git(root,'checkout','-b','progress/work')
    upstream=repository_git.prepare(root,print)
    (root/'README.md').write_text('progress checkpoint')
    result=repository_git.finish(root,{'README.md'},upstream,print)
    assert result['pushed']
    assert git(remote,'rev-parse','refs/heads/progress/work')==result['commit']
    assert git(remote,'rev-parse','HEAD')==original
    assert git(root,'rev-parse','--abbrev-ref','@{upstream}')=='origin/progress/work'


@pytest.mark.parametrize('remotes',[0,2])
def test_missing_upstream_requires_a_clear_remote(checkout,remotes):
    root,_=checkout
    git(root,'branch','--unset-upstream')
    if remotes==0:git(root,'remote','remove','origin')
    else:git(root,'remote','add','another','/unused')
    head=git(root,'rev-parse','HEAD')
    with pytest.raises(NetworkError,match='no upstream'):
        repository_git.prepare(root,print)
    assert git(root,'rev-parse','HEAD')==head


def test_partial_upstream_configuration_is_not_replaced(checkout):
    root,_=checkout
    branch=git(root,'branch','--show-current')
    git(root,'config','--unset',f'branch.{branch}.merge')
    with pytest.raises(NetworkError,match='Configure this branch'):
        repository_git.prepare(root,print)
    assert git(root,'config','--get',f'branch.{branch}.remote')=='origin'


def test_missing_upstream_still_rejects_remote_ahead(checkout,tmp_path):
    root,remote=checkout
    git(root,'branch','--unset-upstream')
    other=tmp_path/'other'
    subprocess.run(['git','clone','-q',str(remote),str(other)],check=True)
    (other/'remote.txt').write_text('a remote change')
    git(other,'add','.')
    git(other,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','remote')
    git(other,'push')
    before=git(root,'rev-parse','HEAD')
    with pytest.raises(NetworkError,match='ahead or has diverged'):
        repository_git.prepare(root,print)
    assert git(root,'rev-parse','HEAD')==before


def test_expected_missing_config_does_not_hide_real_git_errors(checkout):
    root,_=checkout
    assert repository_git.git(root,'config','--get','nonexistent.key',missing=True) is None
    with pytest.raises(NetworkError,match='Git config failed'):
        repository_git.git(root,'config','--get','invalid key',missing=True)


def test_failed_first_push_does_not_set_upstream(checkout):
    root,remote=checkout
    git(root,'branch','--unset-upstream')
    upstream=repository_git.prepare(root,print)
    hook=remote/'hooks/pre-receive';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
    (root/'README.md').write_text('new progress')
    result=repository_git.finish(root,{'README.md'},upstream,print)
    assert not result['pushed']
    assert repository_git.git(root,'config','--get',f"branch.{git(root,'branch','--show-current')}.remote",missing=True) is None
    assert git(root,'rev-parse','HEAD')==result['commit']
    hook.unlink()
    assert repository_git.finish(root,{'README.md'},repository_git.prepare(root,print),print)['pushed']


def test_configured_upstream_is_respected_with_multiple_remotes(checkout):
    root,remote=checkout
    git(root,'remote','rename','origin','destination')
    git(root,'remote','add','origin','/unused')
    branch=git(root,'branch','--show-current')
    upstream=repository_git.prepare(root,print)
    assert upstream==('destination','refs/heads/'+branch)
    (root/'README.md').write_text('progress')
    result=repository_git.finish(root,{'README.md'},upstream,print)
    assert result['pushed'] and git(remote,'rev-parse','HEAD')==result['commit']
    assert git(root,'config','--get',f'branch.{branch}.remote')=='destination'


def test_missing_identity_has_actionable_error(checkout):
    root,_=checkout
    git(root,'config','user.name','')
    with pytest.raises(NetworkError,match='user.name and user.email'):
        repository_git.prepare(root,print)
