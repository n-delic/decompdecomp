"""Separate CPU work from wall-clock stalls without logging SQL or changing transactions."""
import logging
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from decompnet import db_diagnostics
from decompnet import store as module
from decompnet.store import Store


class Clock:
    wall = 100.
    cpu = 20.

    def advance(self, wall, cpu):
        self.wall += wall
        self.cpu += cpu


def clocks(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.wall,
                                                       perf_counter=lambda: clock.wall))
    monkeypatch.setattr(module, 'thread_time', lambda: clock.cpu, raising=False)
    return clock


@pytest.mark.parametrize('fail_fetch', [False, True])
def test_slow_read_reports_cpu_and_wall_phases_and_closes_failed_fetch(tmp_path, monkeypatch, caplog, fail_fetch):
    store = Store(tmp_path / 'read.db')
    clock = clocks(monkeypatch)
    closed = []

    class Cursor:
        def fetchone(self):
            clock.advance(8, .02)
            if fail_fetch:raise sqlite3.OperationalError('synthetic fetch failure')
            return {'n': 1}

        def close(self):
            closed.append(True)
            clock.advance(1, .03)

    class Connection:
        def execute(self, sql, args):
            clock.advance(5, 2)
            return Cursor()

    def reader():
        clock.advance(4, .01)
        return Connection()

    monkeypatch.setattr(store, '_reader', reader)
    monkeypatch.setattr(store, '_plan_shape', lambda *args: 'plan=unavailable')
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        if fail_fetch:
            with pytest.raises(sqlite3.OperationalError, match='synthetic fetch failure'):
                store.one('SELECT private_column FROM private_table WHERE password=?', 'secret-binding')
        else:
            assert store.one('SELECT private_column FROM private_table WHERE password=?', 'secret-binding') == {'n': 1}
    text = next(r.getMessage() for r in caplog.records if 'Slow database read' in r.getMessage())
    assert 'read 18.000s' in text
    assert 'thread_cpu=2.060s' in text
    assert 'connection=4.000s' in text
    assert 'execute=5.000s' in text
    assert 'fetch=9.000s' in text
    assert 'private_column' not in text and 'private_table' not in text and 'secret-binding' not in text
    assert closed == [True]
    store.conn.close()


@pytest.mark.parametrize('rollback', [False, True])
def test_writer_cpu_excludes_admission_and_keeps_commit_or_rollback(tmp_path, monkeypatch, caplog, rollback):
    store = Store(tmp_path / 'write.db')
    clock = clocks(monkeypatch)
    db_diagnostics._last.clear()

    class Gate:
        @contextmanager
        def hold(self, **kwargs):
            clock.advance(100, 3)  # queue time and CPU precede the transaction
            yield True

    monkeypatch.setattr(store, 'writer_gate', Gate())
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        try:
            with store.tx() as db:
                db.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('secret-key', 'secret-value'))
                clock.advance(10, .02)  # a blocked transaction: long wall time, little CPU
                if rollback:raise ValueError('synthetic cancellation')
        except ValueError:
            assert rollback
    text = next(r.getMessage() for r in caplog.records if 'Long database write' in r.getMessage())
    assert 'write 10.000s' in text and 'thread_cpu=0.020s' in text
    assert 'secret-key' not in text and 'secret-value' not in text
    assert bool(store.one('SELECT 1 FROM settings WHERE key=?', 'secret-key')) is not rollback
    store.conn.close()
