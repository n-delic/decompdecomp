"""Corruption reports must name the damaged file and preserve recovery evidence."""
import sqlite3

import pytest

from decompnet.store import CorruptDatabaseError, Store, corrupt_database


def test_corrupt_database_preserves_file_and_names_it(tmp_path):
    path = tmp_path / 'damaged.sqlite3'
    contents = b'not a SQLite database' * 200
    path.write_bytes(contents)
    with pytest.raises(CorruptDatabaseError) as failure:Store(path, require_existing=True)
    assert path.read_bytes() == contents
    assert str(path) in str(failure.value)
    assert '-wal' in str(failure.value) and 'do not delete' in str(failure.value)
    assert isinstance(failure.value.__cause__, sqlite3.DatabaseError)


def test_checkpoint_corruption_is_named_and_connection_closed(tmp_path, monkeypatch):
    closed = []
    error = sqlite3.DatabaseError('database disk image is malformed')
    error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
    def initialize(store):
        class Connection:
            def close(self):closed.append(True)
        store.conn = Connection()
        raise error
    monkeypatch.setattr(Store, '_initialize', initialize)
    with pytest.raises(CorruptDatabaseError) as failure:Store(tmp_path/'project.sqlite3')
    assert closed == [True] and failure.value.__cause__ is error
    assert corrupt_database(error)


def test_busy_database_is_not_misreported_as_corruption(tmp_path, monkeypatch):
    error = sqlite3.OperationalError('database is locked')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    def initialize(store):raise error
    monkeypatch.setattr(Store, '_initialize', initialize)
    with pytest.raises(sqlite3.OperationalError) as failure:Store(tmp_path/'project.sqlite3')
    assert failure.value is error


def test_startup_reports_corruption_without_leaving_server_state(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from decompnet import compiler_processes
    from decompnet.app import cleanup, cli, engine
    sock = Mock()
    sock.getsockname.return_value = ('127.0.0.1', 9999)
    monkeypatch.setattr(cli, '_listen', lambda *a, **kw:sock)
    monkeypatch.setattr(cleanup, 'old_registrations', lambda *_:None)
    monkeypatch.setattr(compiler_processes, 'configure', lambda *_:None)
    path = tmp_path/'damaged.sqlite3'
    def broken(*a, **kw):raise CorruptDatabaseError(path, project='re-volt')
    monkeypatch.setattr(engine, 'RomNetwork', broken)
    state = tmp_path/'server.json'
    log = Mock()
    assert cli._start(SimpleNamespace(port=9999), tmp_path, tmp_path, state, log, None, False) == 1
    assert not state.exists()
    sock.close.assert_called_once()
    log.exception.assert_called_once()
    terminal = capsys.readouterr().err
    assert 're-volt' in terminal and str(path) in terminal and 'Traceback' not in terminal
