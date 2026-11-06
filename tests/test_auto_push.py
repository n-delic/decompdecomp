"""Automatic push uses synthetic progress and local bare remotes, never live game projects."""
import threading

import pytest

from decompnet.app import auto_push, match_commits, repository_push, saves
from decompnet.network import NetworkError

from . import test_match_commits, test_repository_progress
from .test_repository_push import commit

automatic = test_match_commits.automatic
progress = test_repository_progress.progress
git = test_repository_progress.git


def ready(net):
    net._update_cfg('game', lambda cfg: cfg['auto_push'].pop('next_check', None))


def test_default_off_then_pushes_at_ten_without_staging_dirty_files(progress):
    net, root, remote = progress
    assert auto_push.status(net, net.project_row('game')) == dict(enabled=False, threshold=10, error='', last_push='', phase='')
    before = git(remote, 'rev-parse', 'HEAD')
    for number in range(9):commit(root, 'local.txt', f'commit {number}')
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == before
    state = auto_push.configure(net, 'game', True, folder=str(root))
    assert state['threshold'] == 10
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == before
    head = commit(root, 'local.txt', 'tenth commit')
    (root/'private.txt').write_text('unfinished')
    git(root, 'add', 'private.txt')
    dirty = git(root, 'status', '--porcelain')
    ready(net)
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == head
    assert git(root, 'status', '--porcelain') == dirty
    assert auto_push.status(net, net.project_row('game'))['last_push'] == head
    assert 'private.txt' not in git(remote, 'ls-tree', '-r', '--name-only', 'HEAD')


def test_custom_threshold_counts_remaining_commits_after_a_manual_push(progress):
    net, root, remote = progress
    auto_push.configure(net, 'game', True, 2, str(root))
    head = commit(root)
    repository_push.run(net, 'game', str(root), lambda line: None)
    commit(root, text='first after push')
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == head
    head = commit(root, text='second after push')
    ready(net)
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == head


@pytest.mark.parametrize('value', [True, False, 0, -1, 10001, 1.5, '10', None])
def test_threshold_validation(progress, value):
    net, root, _ = progress
    with pytest.raises(NetworkError, match='threshold'):
        auto_push.configure(net, 'game', True, value, str(root))


def test_missing_origin_and_changed_destination_pause_without_pushing(progress):
    net, root, remote = progress
    git(root, 'remote', 'remove', 'origin')
    with pytest.raises(NetworkError, match='origin'):
        auto_push.configure(net, 'game', True, 1, str(root))
    git(root, 'remote', 'add', 'origin', str(remote))
    auto_push.configure(net, 'game', True, 1, str(root))
    commit(root)
    before = git(remote, 'rev-parse', 'HEAD')
    git(root, 'switch', '-c', 'another-branch')
    auto_push.process(net, 'game')
    assert 'branch or origin changed' in auto_push.status(net, net.project_row('game'))['error']
    assert git(remote, 'rev-parse', 'HEAD') == before


def test_disable_during_fetch_cancels_the_push(progress, monkeypatch):
    net, root, remote = progress
    auto_push.configure(net, 'game', True, 1, str(root))
    commit(root)
    before = git(remote, 'rev-parse', 'HEAD')
    real = repository_push.git
    def checking(root, *args, **kwargs):
        result = real(root, *args, **kwargs)
        if args[0] == 'fetch':auto_push.configure(net, 'game', False)
        return result
    monkeypatch.setattr(repository_push, 'git', checking)
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == before
    assert not auto_push.status(net, net.project_row('game'))['enabled']


def test_failed_push_retries_later_and_retains_the_commit(progress, monkeypatch):
    net, root, remote = progress
    auto_push.configure(net, 'game', True, 1, str(root))
    head = commit(root)
    calls = []
    real = repository_push.git
    def offline(root, *args, **kwargs):
        if args[0] == 'fetch':
            calls.append('fetch')
            raise NetworkError('Origin unavailable. Retry when online.')
        return real(root, *args, **kwargs)
    monkeypatch.setattr(repository_push, 'git', offline)
    auto_push.process(net, 'game')
    auto_push.process(net, 'game')
    assert calls == ['fetch']
    assert 'unavailable' in auto_push.status(net, net.project_row('game'))['error']
    assert git(root, 'rev-parse', 'HEAD') == head != git(remote, 'rev-parse', 'HEAD')
    monkeypatch.setattr(repository_push, 'git', real)
    ready(net)
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == head
    assert not auto_push.status(net, net.project_row('game'))['error']


def test_imported_project_commits_can_auto_push(progress):
    net, root, remote = progress
    for platform in ('gc', 'wii', 'pc', 'nds', 'ps3', 'xbox'):
        net._update_cfg('game', lambda cfg, platform=platform: cfg.update(kind='fork', platform=platform))
        auto_push.configure(net, 'game', True, 1, str(root))
        head = commit(root, text='Project commit for '+platform)
        auto_push.process(net, 'game')
        assert git(remote, 'rev-parse', 'HEAD') == head


def test_push_worker_runs_outside_the_autosave_lane_and_stops(progress, monkeypatch):
    net, root, _ = progress
    auto_push.configure(net, 'game', True, 1, str(root))
    worker = saves.Autosave(net)
    called = threading.Event()
    monkeypatch.setattr(worker, '_loop', lambda: None)
    monkeypatch.setattr(auto_push, 'process', lambda *args: called.set())
    worker.lane.acquire()
    try:
        worker.start()
        assert called.wait(2), 'Push must not wait for a slow save in the shared lane'
    finally:
        worker.close()
        worker.lane.release()
        worker.push_thread.join(2)
    assert not worker.push_thread.is_alive()


def test_origin_url_change_pauses_without_contacting_the_new_remote(progress, monkeypatch):
    net, root, remote = progress
    auto_push.configure(net, 'game', True, 1, str(root))
    commit(root)
    before = git(remote, 'rev-parse', 'HEAD')
    git(root, 'remote', 'set-url', 'origin', '/nonexistent/new-destination')
    def no_push(*args, **kwargs):raise AssertionError('Changed origins must never be contacted')
    monkeypatch.setattr(repository_push, 'run', no_push)
    auto_push.process(net, 'game')
    assert 'branch or origin changed' in auto_push.status(net, net.project_row('game'))['error']
    assert git(remote, 'rev-parse', 'HEAD') == before
    with pytest.raises(NetworkError, match='Disable and re-enable'):
        auto_push.configure(net, 'game', True, 2, str(root))


def test_remote_divergence_never_force_pushes(progress):
    net, root, remote = progress
    auto_push.configure(net, 'game', True, 1, str(root))
    commit(root)
    git(root, 'push', 'origin', 'HEAD:refs/heads/remote-only')
    remote_only = git(root, 'rev-parse', 'HEAD')
    git(root, 'reset', '--soft', 'HEAD^')
    commit(root, text='different local history')
    git(remote, 'update-ref', 'refs/heads/'+git(root, 'branch', '--show-current'), remote_only)
    auto_push.process(net, 'game')
    assert 'diverged' in auto_push.status(net, net.project_row('game'))['error']
    assert git(remote, 'rev-parse', 'HEAD') == remote_only


def test_two_verified_matches_are_committed_cleaned_and_then_pushed(automatic):
    net, root, remote, _ = automatic
    test_match_commits.enable(automatic)
    repository_push.run(net, 'game', str(root), lambda line: None)
    before = git(remote, 'rev-parse', 'HEAD')
    auto_push.configure(net, 'game', True, 2, str(root))
    test_match_commits.match(automatic, 'f', 'Preserve signed comparison')
    match_commits.process(net, 'game')
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == before
    test_match_commits.match(automatic, 'g', 'Preserve load order')
    match_commits.process(net, 'game')
    ready(net)
    auto_push.process(net, 'game')
    assert git(remote, 'rev-parse', 'HEAD') == git(root, 'rev-parse', 'HEAD')
    assert git(remote, 'log', '-2', '--format=%s').splitlines() == ['Preserve load order', 'Preserve signed comparison']
    assert not (root/'best/f.c').exists() and not (root/'best/g.c').exists()
