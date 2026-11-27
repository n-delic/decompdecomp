"""The batch script follows each app job before starting the next one."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('cross_game_all', Path(__file__).resolve().parents[1] / 'tools/cross_game_all.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def test_runs_jobs_in_sequence_and_continues_after_failure(monkeypatch):
    calls = []
    replies = iter([{'job': 'one'}, {'state': 'running'}, {'state': 'done', 'result': {'matched': 1}},
                    {'job': 'two'}, {'state': 'error', 'error': 'unavailable'},
                    {'job': 'three'}, {'state': 'done'}])

    def call(info, path, body=None):
        calls.append((path, body))
        return next(replies)

    monkeypatch.setattr(batch, 'call', call)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    failures = batch.run({}, [{'slug': 'a'}, {'slug': 'b'}, {'slug': 'c'}])
    assert failures == ['b']
    assert [path for path, _ in calls] == ['/api/cross-game/run', '/api/job?id=one', '/api/job?id=one',
                                           '/api/cross-game/run', '/api/job?id=two',
                                           '/api/cross-game/run', '/api/job?id=three']


def test_interrupt_cancels_current_job(monkeypatch):
    calls = []

    def call(info, path, body=None):
        calls.append((path, body))
        if path == '/api/cross-game/run':
            return {'job': 'one'}
        if path.startswith('/api/job?'):
            raise KeyboardInterrupt
        return {}

    monkeypatch.setattr(batch, 'call', call)
    try:
        batch.run({}, [{'slug': 'a'}])
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('interrupt was swallowed')
    assert calls[-1] == ('/api/job/cancel', {'id': 'one'})


def test_retries_worker_death_before_next_project(monkeypatch):
    replies = iter([{'job': 'first'}, {'state': 'error', 'error': 'The background worker stopped during a pass.'},
                    {'job': 'retry'}, {'state': 'done'}, {'job': 'second'}, {'state': 'done'}])
    calls = []

    def call(info, path, body=None):
        calls.append((path, body))
        return next(replies)

    monkeypatch.setattr(batch, 'call', call)
    assert batch.run({}, [{'slug': 'a'}, {'slug': 'b'}]) == []
    assert [body['slug'] for path, body in calls if path == '/api/cross-game/run'] == ['a', 'a', 'b']


def test_shutdown_aborts_batch(monkeypatch):
    def call(info, path, body=None):
        raise RuntimeError('decompdecomp is shutting down. Reopen the app before starting work.')

    monkeypatch.setattr(batch, 'call', call)
    import pytest

    with pytest.raises(RuntimeError, match='batch stopped at a'):
        batch.run({}, [{'slug': 'a'}, {'slug': 'b'}])
