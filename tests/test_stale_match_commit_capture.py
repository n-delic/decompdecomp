"""Stale match commit retries must not start another saved-progress verification."""
from types import SimpleNamespace

import pytest

from decompnet.app import match_commits, repository_progress
from decompnet.network import NetworkError

from . import test_match_commits

automatic = test_match_commits.automatic
progress = test_match_commits.progress
enable = test_match_commits.enable
git = test_match_commits.git
match = test_match_commits.match


@pytest.mark.parametrize('status', [None, 'open', 'asm'])
def test_stale_job_is_rejected_before_capture(monkeypatch, tmp_path, status):
    row = None if status is None else {'status':status}
    net = SimpleNamespace(project_store=lambda _:SimpleNamespace(one=lambda *args:row))
    def unexpected(*args, **kwargs):
        pytest.fail('A stale match must not capture or synchronize saved progress')
    monkeypatch.setattr(repository_progress, 'snapshot', unexpected)
    monkeypatch.setattr(match_commits.repository_git, 'git', unexpected)
    with pytest.raises(NetworkError, match='queued function no longer matches'):
        match_commits.scoped_snapshot(net, {'id':1,'slug':'game'}, tmp_path,
                                     {'function_id':1}, lambda _:None)


def test_job_is_rechecked_after_progress_capture(automatic, monkeypatch):
    net, root, _, _ = automatic
    enable(automatic)
    match(automatic)
    before = git(root, 'rev-parse', 'HEAD')
    original = repository_progress.snapshot
    captures = []
    def demoted(*args, **kwargs):
        result = original(*args, **kwargs)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='open' WHERE name='f'")
        captures.append(True)
        return result
    monkeypatch.setattr(repository_progress, 'snapshot', demoted)
    match_commits.process(net, 'game')
    assert captures == [True]
    assert git(root, 'rev-parse', 'HEAD') == before
    job = net.store.one('SELECT state,error FROM auto_match_commits')
    assert job['state'] == 'pending'
    assert 'queued function no longer matches' in job['error']
