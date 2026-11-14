"""The log names each cause of a slow database: long writes, writers waiting, an unemptied log, scanning reads."""
import logging
import sqlite3
import threading
import time

import pytest

from decompnet import db_diagnostics
from decompnet.store import Store


@pytest.fixture(autouse=True)
def fresh_limits():
    db_diagnostics._last.clear()
    yield
    db_diagnostics._last.clear()


def store_with_rows(tmp_path):
    store = Store(tmp_path / 'slow.db')
    with store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,root,config) VALUES(1,?,?,?)', ('p', str(tmp_path), '{}'))
    return store


def test_long_write_names_where_it_began(tmp_path, monkeypatch, caplog):
    store = store_with_rows(tmp_path)
    monkeypatch.setattr(db_diagnostics, 'LONG_WRITE_SECONDS', 0.01)
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        with store.tx() as db:
            db.execute("UPDATE projects SET config='{\"secret\": 1}' WHERE id=1")
            time.sleep(0.03)
    record = next(r for r in caplog.records if 'Long database write' in r.getMessage())
    assert 'test_db_diagnostics.py' in record.getMessage()
    assert 'test_long_write_names_where_it_began' in record.getMessage()
    assert 'secret' not in caplog.text and 'projects' not in caplog.text


def test_waiting_writer_names_the_transaction_it_waited_behind(tmp_path, monkeypatch, caplog):
    store = store_with_rows(tmp_path)
    monkeypatch.setattr(db_diagnostics, 'WRITE_WAIT_SECONDS', 0.01)
    entered = threading.Event()

    def slow_holder():
        with store.tx() as db:
            db.execute("UPDATE projects SET name='a' WHERE id=1")
            entered.set()
            time.sleep(0.1)

    holder = threading.Thread(target=slow_holder)
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        holder.start()
        entered.wait(5)
        with store.tx() as db:
            db.execute("UPDATE projects SET name='b' WHERE id=1")
        holder.join()
    message = next(r.getMessage() for r in caplog.records if 'Database write waited' in r.getMessage())
    assert 'behind test_db_diagnostics.py' in message and 'slow_holder' in message


def test_slow_read_reports_its_plan_shape_without_table_names(tmp_path, monkeypatch, caplog):
    store = store_with_rows(tmp_path)
    from types import SimpleNamespace

    from decompnet import store as module
    stamps = iter([0, 1])
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: next(stamps), perf_counter=time.perf_counter))
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        store.all("SELECT slug FROM projects WHERE config LIKE ? ORDER BY name", '%private%')
    message = next(r.getMessage() for r in caplog.records if 'Slow database read' in r.getMessage())
    assert 'full_scans=1' in message and 'temp_sorts=1' in message and 'via=reader' in message
    assert 'projects' not in message and 'private' not in message


def test_plan_shape_tells_index_searches_from_scans():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE t(a INTEGER PRIMARY KEY, b)')
    assert 'full_scans=0' in db_diagnostics.plan_shape(conn, 'SELECT b FROM t WHERE a=?', (1,))
    assert 'index_searches=1' in db_diagnostics.plan_shape(conn, 'SELECT b FROM t WHERE a=?', (1,))
    assert 'full_scans=1' in db_diagnostics.plan_shape(conn, 'SELECT a FROM t WHERE b=?', (1,))
    assert db_diagnostics.plan_shape(conn, 'not sql', ()) == 'plan=unavailable'


def test_log_held_by_a_read_is_reported(tmp_path, monkeypatch, caplog):
    store = store_with_rows(tmp_path)
    monkeypatch.setattr(db_diagnostics, 'WAL_WARN_BYTES', 1)
    store._wal_watch.next = 0  # the setup's commit already looked
    reader = sqlite3.connect(tmp_path / 'slow.db', isolation_level=None)
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM projects').fetchall()  # holds its snapshot: later pages can't be checkpointed
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        with store.tx() as db:
            db.execute("UPDATE projects SET name='after the read' WHERE id=1")
    reader.execute('COMMIT')
    reader.close()
    assert any('Database log not checkpointed' in r.getMessage() for r in caplog.records)


def test_emptied_log_is_quiet(caplog):
    with caplog.at_level(logging.WARNING, logger='decompdecomp'):
        db_diagnostics.report_wal('x', 0, 100_000, 100_000, 4096)  # large but fully copied
        db_diagnostics.report_wal('x', 0, 10, 0, 4096)  # small
    assert not caplog.records
