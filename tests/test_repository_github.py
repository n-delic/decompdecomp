"""Remote creation cannot upload files or connect an unintended repository."""
from types import SimpleNamespace

import pytest

from decompnet.app import repository_github
from decompnet.network import NetworkError


@pytest.fixture
def github(monkeypatch, tmp_path):
    calls, remotes = [], []
    monkeypatch.setattr(repository_github.shutil, 'which', lambda name: '/tools/gh')
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='https://github.com/person/progress\n')
    monkeypatch.setattr(repository_github.subprocess, 'run', run)
    monkeypatch.setattr(repository_github, 'git', lambda *args, **kwargs: remotes.append(args))
    return tmp_path, calls, remotes


@pytest.mark.parametrize('visibility', ['private', 'public'])
def test_create_connects_without_upload(github, visibility):
    root, calls, remotes = github
    repository_github.create(root, 'person/progress', visibility, lambda line: None)
    assert calls[0][0][1:] == ['auth', 'status', '--hostname', 'github.com']
    assert calls[1][0][1:] == ['repo', 'create', 'person/progress', '--'+visibility]
    assert calls[1][1]['env']['GH_PROMPT_DISABLED'] == '1'
    assert calls[1][1]['env']['GH_HOST'] == 'github.com'
    assert remotes == [(root, 'remote', 'add', '--', 'origin', 'https://github.com/person/progress.git')]


@pytest.mark.parametrize('name,visibility', [('--push','private'), ('a/b/c','private'), ('ok','internal'), ('a\nb','private')])
def test_invalid_settings_do_not_contact_github(github, name, visibility):
    root, calls, remotes = github
    with pytest.raises(NetworkError):repository_github.create(root, name, visibility, print)
    assert not calls and not remotes


@pytest.mark.parametrize('failure', ['auth', 'create', 'url', 'timeout'])
def test_errors_never_connect_remote(github, monkeypatch, failure):
    root, calls, remotes = github
    def run(args, **kwargs):
        if failure == 'timeout':raise repository_github.subprocess.TimeoutExpired(args, 60)
        bad = failure == 'auth' or failure == 'create' and args[1] == 'repo'
        return SimpleNamespace(returncode=int(bad), stdout='https://elsewhere.invalid/private' if failure == 'url' else '')
    monkeypatch.setattr(repository_github.subprocess, 'run', run)
    with pytest.raises(NetworkError):repository_github.create(root, 'progress', 'private', print)
    assert not remotes


def test_missing_cli_explains_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(repository_github.shutil, 'which', lambda name: None)
    monkeypatch.setattr(repository_github.Path, 'is_file', lambda path: False)
    with pytest.raises(NetworkError, match='Install GitHub CLI'):
        repository_github.create(tmp_path, 'progress', 'private', print)


def test_cancel_before_creation(github):
    root, calls, remotes = github
    class Log:
        def __call__(self, message):pass
        def check_cancel(self):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        repository_github.create(root, 'progress', 'private', Log())
    assert len(calls) == 1 and not remotes
