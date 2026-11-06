"""Source downloads use scratch folders and fake Git, never the user's app or network."""
import subprocess
from types import SimpleNamespace

import pytest

from decompnet.app import source_checkout, updates


@pytest.fixture
def checkout(monkeypatch):
    monkeypatch.setattr(updates, 'installed', lambda: {'repo': 'example/fork', 'channel': 'branch', 'branch': 'feature/ui'})
    monkeypatch.setattr(source_checkout.shutil, 'which', lambda _: '/fake/git')
    class Thread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(source_checkout.threading, 'Thread', Thread)
    return source_checkout.Checkout()


def test_download_uses_configured_repo_branch_and_literal_destination(checkout, tmp_path, monkeypatch):
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs['timeout'] == 600 and 'shell' not in kwargs
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(source_checkout.subprocess, 'run', run)
    path = tmp_path / "checkout with 'quotes' $values"
    result = checkout.start(str(path))
    assert commands == [['/fake/git', 'clone', '--depth', '1', '--single-branch', '--branch', 'feature/ui', '--',
                         'https://github.com/example/fork.git', str(path)]]
    assert result['job']['state'] == 'done' and path.is_dir()
    assert 'uv sync --python 3.12\nuv run decomp' in result['job']['commands']
    assert result['job']['path'] == str(path)
    assert 'example/fork' == result['repo']


@pytest.mark.parametrize('destination', ['', 'relative/folder'])
def test_rejects_missing_or_relative_path(checkout, destination):
    with pytest.raises(ValueError, match='path'):
        checkout.start(destination)


def test_existing_files_and_symlinks_are_never_overwritten(checkout, tmp_path):
    target = tmp_path / 'existing';target.mkdir();(target/'precious').write_text('keep')
    for path in [target, tmp_path/'link']:
        if path != target:path.symlink_to(tmp_path/'missing')
        with pytest.raises(ValueError, match='already exists'):checkout.start(str(path))
    assert (target/'precious').read_text() == 'keep'
    with pytest.raises(ValueError, match='parent folder'):checkout.start(str(tmp_path/'absent'/'checkout'))


def test_missing_git_invalid_branch_and_duplicate_job_fail_before_creating(checkout, tmp_path, monkeypatch):
    path = tmp_path/'new'
    monkeypatch.setattr(source_checkout.shutil, 'which', lambda _: None)
    with pytest.raises(ValueError, match='Git is not installed'):checkout.start(str(path))
    monkeypatch.setattr(updates, 'installed', lambda: {'repo': 'example/fork', 'channel': 'branch', 'branch': '--bad'})
    with pytest.raises(ValueError, match='valid update branch'):checkout.start(str(path))
    checkout.job = {'state': 'cloning'}
    with pytest.raises(ValueError, match='already running'):checkout.start(str(path))
    assert not path.exists()


def test_failure_preserves_partial_checkout_and_allows_a_new_destination(checkout, tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        from pathlib import Path
        (Path(cmd[-1])/'partial').write_text('downloaded')
        return SimpleNamespace(returncode=1, stderr='network unavailable')
    monkeypatch.setattr(source_checkout.subprocess, 'run', run)
    result = checkout.start(str(tmp_path/'failed'))
    assert result['job']['state'] == 'error' and 'network unavailable' in result['job']['error']
    assert (tmp_path/'failed'/'partial').read_text() == 'downloaded'
    with pytest.raises(ValueError, match='already exists'):checkout.start(str(tmp_path/'failed'))
    monkeypatch.setattr(source_checkout.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    assert checkout.start(str(tmp_path/'retry'))['job']['state'] == 'done'


def test_timeout_removes_only_empty_reservation(checkout, tmp_path, monkeypatch):
    def run(*args, **kwargs): raise subprocess.TimeoutExpired('git', 600)
    monkeypatch.setattr(source_checkout.subprocess, 'run', run)
    assert checkout.start(str(tmp_path/'timeout'))['job']['state'] == 'error'
    assert not (tmp_path/'timeout').exists()


def test_release_download_uses_main_and_rejects_non_github_source(checkout, monkeypatch):
    monkeypatch.setattr(updates, 'installed', lambda: {'repo': 'example/fork', 'channel': 'release', 'branch': ''})
    assert checkout.status()['branch'] == 'main'
    monkeypatch.setattr(updates, 'installed', lambda: {'repo': 'https://evil.invalid/fork', 'channel': 'release'})
    with pytest.raises(ValueError):checkout.status()


def test_checkout_dialog_download_progress_copy_and_errors():
    import shutil
    from pathlib import Path
    node = shutil.which('node')
    if not node:pytest.skip('Node is needed for UI logic checks')
    subprocess.run([node, str(Path(__file__).with_name('gui_source_checkout.js'))], check=True, capture_output=True, timeout=30)
