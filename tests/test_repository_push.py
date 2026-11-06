"""Push-only actions use local bare remotes and preserve every uncommitted byte."""
import subprocess

import pytest

from decompnet.app import repository_push
from decompnet.network import NetworkError

from . import test_repository_progress

progress = test_repository_progress.progress
git = test_repository_progress.git


def commit(root, name='local.txt', text='local progress'):
    (root/name).write_text(text)
    git(root, 'add', name)
    git(root, 'commit', '-qm', text)
    return git(root, 'rev-parse', 'HEAD')


def test_status_and_push_only_existing_commits_preserves_dirty_and_staged_files(progress):
    net, root, remote = progress
    head = commit(root)
    (root/'private.txt').write_text('unfinished private work')
    git(root, 'add', 'private.txt')
    dirty = git(root, 'status', '--porcelain')
    state = repository_push.status(net, 'game', str(root))
    assert state['unpushed'] == 1 and state['can_push']
    assert state['commits'][0]['sha'] == head
    result = repository_push.run(net, 'game', str(root), lambda line: None)
    assert result['pushed'] and result['status']['unpushed'] == 0
    assert git(remote, 'rev-parse', 'HEAD') == head == git(root, 'rev-parse', 'HEAD')
    assert git(root, 'status', '--porcelain') == dirty
    assert 'private.txt' not in git(remote, 'ls-tree', '-r', '--name-only', 'HEAD')


def test_failed_push_keeps_local_commits(progress):
    net, root, remote = progress
    before = git(remote, 'rev-parse', 'HEAD')
    head = commit(root)
    hook = remote/'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o755)
    with pytest.raises(NetworkError, match='remain local'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == before
    assert git(root, 'rev-parse', 'HEAD') == head
    assert repository_push.status(net, 'game', str(root))['unpushed'] == 1


def test_remote_divergence_is_not_force_pushed(progress, tmp_path):
    net, root, remote = progress
    other = tmp_path/'other'
    subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
    git(other, 'config', 'user.name', 'Other');git(other, 'config', 'user.email', 'other@example.invalid')
    remote_head = commit(other, 'remote.txt', 'remote progress')
    git(other, 'push', 'origin', 'HEAD')
    local = commit(root)
    with pytest.raises(NetworkError, match='ahead or has diverged'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == remote_head
    assert git(root, 'rev-parse', 'HEAD') == local
    state = repository_push.status(net, 'game', str(root))
    assert state['behind'] == 1 and not state['can_push']


def test_new_branch_is_pushed_to_origin_without_changing_other_upstream(progress):
    net, root, remote = progress
    git(root, 'checkout', '-qb', 'work/matches')
    git(root, 'remote', 'add', 'other', str(root.parent/'unused.git'))
    git(root, 'config', 'branch.work/matches.remote', 'other')
    git(root, 'config', 'branch.work/matches.merge', 'refs/heads/elsewhere')
    head = commit(root)
    state = repository_push.status(net, 'game', str(root))
    assert state['new_branch'] and state['unpushed'] == 1 and state['destination'] == 'origin/work/matches'
    repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'refs/heads/work/matches') == head
    assert git(root, 'config', '--get', 'branch.work/matches.remote') == 'other'


def test_detached_missing_origin_and_unlinked_paths_have_actionable_errors(progress, tmp_path):
    net, root, _ = progress
    with pytest.raises(NetworkError, match='linked checkout'):
        repository_push.status(net, 'game', str(tmp_path))
    git(root, 'checkout', '--detach')
    with pytest.raises(NetworkError, match='named Git branch'):
        repository_push.status(net, 'game', str(root))
    git(root, 'checkout', '-b', 'work/test')
    git(root, 'remote', 'rename', 'origin', 'other')
    with pytest.raises(NetworkError, match='origin remote'):
        repository_push.status(net, 'game', str(root))


def test_commits_created_during_fetch_remain_unpushed(progress, monkeypatch):
    net, root, remote = progress
    head = commit(root)
    original = repository_push.git
    later = []
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if args[0] == 'fetch':later.append(commit(root, 'later.txt', 'newer automatic match'))
        return result
    monkeypatch.setattr(repository_push, 'git', changed)
    result = repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == head
    assert git(root, 'rev-parse', 'HEAD') == later[0]
    assert result['status']['unpushed'] == 1


def test_imported_project_checkout_can_push_its_existing_commits(progress):
    net, root, _ = progress
    with net.store.tx() as db:
        db.execute("UPDATE projects SET root=?,config=json_set(config,'$.kind','fork') WHERE slug='game'", (str(root),))
    commit(root)
    assert repository_push.status(net, 'game')['unpushed'] == 1
    assert repository_push.run(net, 'game', '', lambda line: None)['pushed']


@pytest.mark.parametrize('change', ['branch', 'rewrite'])
def test_branch_change_during_fetch_aborts_without_pushing(progress, monkeypatch, change):
    net, root, remote = progress
    before = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    original = repository_push.git
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if args[0] == 'fetch':
            if change == 'branch':git(root, 'checkout', '-qb', 'work/changed')
            else:git(root, 'reset', '--soft', 'HEAD^')
        return result
    monkeypatch.setattr(repository_push, 'git', changed)
    with pytest.raises(NetworkError, match='changed while checking'):
        repository_push.run(net, 'game', str(root), lambda line: None)
    assert git(remote, 'rev-parse', 'HEAD') == before


class QueueLog:
    def __init__(self):
        import threading
        self.queued = threading.Event()
        self.cancel = threading.Event()
        self.updates = []
    def __call__(self, line):
        self.check_cancel()
    def check_cancel(self):
        if self.cancel.is_set():raise RuntimeError('Push cancelled')
    def progress(self, value):
        self.updates.append(value)
        if value.get('queued'):self.queued.set()


def test_push_waits_for_repository_operation_then_pushes_current_commits(progress):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_sync
    net, root, remote = progress
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        repository_sync._LOCK.acquire()
        try:
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5) and not future.done()
            # A preceding checkpoint can add commits while the push waits.
            latest = commit(root, 'queued.txt', 'checkpoint completed before push')
        finally:
            repository_sync._LOCK.release()
        result = future.result(timeout=10)
    assert result['commit'] == latest == git(remote, 'rev-parse', 'HEAD')
    assert any(u.get('queued') for u in log.updates)
    assert log.updates[-1]['queued'] is False


def test_cancelled_queued_push_does_not_touch_origin_or_release_other_operation(progress):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_push_queue, repository_sync
    net, root, remote = progress
    original = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        repository_sync._LOCK.acquire()
        try:
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5)
            log.cancel.set()
            with pytest.raises(RuntimeError, match='Push cancelled'):future.result(timeout=5)
            assert repository_sync._LOCK.locked()
            assert not repository_push_queue._WAITERS
        finally:
            repository_sync._LOCK.release()
    assert git(remote, 'rev-parse', 'HEAD') == original
    assert repository_push.run(net, 'game', str(root), lambda _:None)['pushed']


@pytest.mark.parametrize('change', ['branch', 'origin'])
def test_destination_changes_while_queued_are_not_pushed(progress, change):
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_sync
    net, root, remote = progress
    original = git(remote, 'rev-parse', 'HEAD')
    commit(root)
    log = QueueLog()
    with ThreadPoolExecutor(1) as pool:
        repository_sync._LOCK.acquire()
        try:
            future = pool.submit(repository_push.run, net, 'game', str(root), log)
            assert log.queued.wait(5)
            if change == 'branch':git(root, 'checkout', '-qb', 'work/different')
            else:git(root, 'remote', 'set-url', 'origin', str(remote.parent/'different.git'))
        finally:
            repository_sync._LOCK.release()
        with pytest.raises(NetworkError, match='changed while this push was queued'):future.result(timeout=5)
    assert git(remote, 'rev-parse', 'HEAD') == original


def test_push_queue_preserves_order_and_recovers_after_failure():
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import repository_push_queue, repository_sync
    logs = [QueueLog(), QueueLog(), QueueLog()]
    order = []
    def work(index):
        with repository_push_queue.turn(logs[index], logs[index].check_cancel):
            order.append(index)
            if index == 0:raise ValueError('earlier push failed')
    with ThreadPoolExecutor(3) as pool:
        repository_sync._LOCK.acquire()
        try:
            futures = []
            for index, log in enumerate(logs):
                futures.append(pool.submit(work, index))
                assert log.queued.wait(5)
        finally:
            repository_sync._LOCK.release()
        with pytest.raises(ValueError, match='earlier push failed'):futures[0].result(timeout=5)
        for future in futures[1:]:future.result(timeout=5)
    assert order == [0,1,2] and not repository_push_queue._WAITERS
    assert not repository_sync._LOCK.locked()
