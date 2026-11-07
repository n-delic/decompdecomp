"""Contention and progress are scoped to a repository, including linked worktrees."""
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from decompnet.app import repository_operations as operations

from .test_repository_push import QueueLog


def test_queued_push_gets_next_turn_and_reports_holder_and_elapsed(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(operations, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    log = QueueLog()
    refreshed = threading.Event()
    original = log.progress
    def progress(value):
        original(value)
        if value.get('wait_seconds') == 65:refreshed.set()
    log.progress = progress
    release = threading.Event()
    def queued_push():
        with operations.push(tmp_path, 'game', log, log.check_cancel):
            assert release.wait(5)
            return True
    with ThreadPoolExecutor(1) as pool:
        with operations.operation(tmp_path, 'game', 'Saving checkpoint', lambda _: None) as holder:
            future = pool.submit(queued_push)
            assert log.queued.wait(5)
            holder.progress({'phase':'Capturing progress files'})
            clock[0] += 65
            assert refreshed.wait(5)
            update = log.updates[-1]
            assert update['blocked_by'] == {'project':'game', 'operation':'Saving checkpoint',
                'phase':'Capturing progress files', 'elapsed_seconds':65}
            assert 'waited 1m 05s' in update['phase']
            assert 'Capturing progress files' in update['phase']
            # Even immediately after the current holder finishes, a background caller cannot jump ahead.
        with operations.operation(tmp_path, 'game', 'Background save', lambda _: None) as background:
            assert background is None
        release.set()
        assert future.result(timeout=5)
    assert not operations._STATES


def test_other_repository_is_independent_and_shared_worktrees_serialize(tmp_path):
    root, other, worktree = [tmp_path/name for name in ('repo', 'other', 'worktree')]
    root.mkdir();other.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'initial', '--allow-empty'], check=True)
    subprocess.run(['git', '-C', str(root), 'worktree', 'add', '-qb', 'other', str(worktree)], check=True)
    assert operations.key(root) == operations.key(worktree)
    alias = tmp_path/'alias';alias.symlink_to(root, target_is_directory=True)
    assert operations.key(root) == operations.key(alias)
    with operations.operation(root, 'first', 'Saving progress', lambda _: None):
        with operations.operation(worktree, 'second', 'Another commit', lambda _: None) as blocked:
            assert blocked is None
        with operations.push(other, 'unrelated', QueueLog(), lambda: None) as independent:
            assert independent is not None
    assert not operations._STATES


def test_failed_operation_releases_its_repository(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        with operations.operation(tmp_path, 'game', 'Saving', lambda _: None):
            raise ValueError('failed snapshot')
    with operations.push(tmp_path, 'game', QueueLog(), lambda: None) as acquired:
        assert acquired is not None
    assert not operations._STATES
