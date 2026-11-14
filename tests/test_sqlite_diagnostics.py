"""Storage failures retain their real SQLite result and expose only bounded filesystem facts."""
import logging
import sqlite3
from types import SimpleNamespace

import pytest

from decompnet import sqlite_diagnostics as diagnostics
from decompnet.app.match_library import Library
from decompnet.payload_storage import Connection


@pytest.fixture(autouse=True)
def reset_rate_limit():
    diagnostics._last.clear()
    yield
    diagnostics._last.clear()


def messages(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith('SQLite storage failure')]


def test_missing_parent_connection_logs_native_code_without_private_path(tmp_path, caplog, monkeypatch):
    secret = 'credential-private-game'
    path = tmp_path / secret / 'project-databases' / '7.sqlite3'
    monkeypatch.setenv('TMPDIR', str(tmp_path / secret))
    monkeypatch.delenv('SQLITE_TMPDIR', raising=False)
    with caplog.at_level(logging.WARNING), pytest.raises(sqlite3.OperationalError) as caught:
        sqlite3.connect(path, factory=Connection)
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
    assert len(messages(caplog)) == 1
    text = messages(caplog)[0]
    assert 'code=14 category=CANTOPEN scope=project operation=connect' in text
    assert 'parent_exists=0' in text and 'configured_temp_exists=0' in text
    assert secret not in text and str(tmp_path) not in text


def test_failed_statement_keeps_exception_and_connection_usable(tmp_path, caplog):
    with sqlite3.connect(tmp_path / '7.transcripts.sqlite3', factory=Connection) as db:
        # The opened main database is fine; a later SQLite file open fails within execute.
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute('ATTACH DATABASE ? AS private_alias', (str(tmp_path / 'private-token' / 'missing.db'),))
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
        assert db.execute('SELECT 42').fetchone() == (42,)
    text = '\n'.join(messages(caplog))
    assert len(messages(caplog)) == 1  # nested serialized execute wrappers don't duplicate it
    assert 'scope=transcripts operation=execute' in text
    assert 'database_exists=1 parent_exists=1' in text
    assert not any(value in text for value in ('private-token', 'ATTACH', 'private_alias', str(tmp_path)))


def test_library_statement_failure_has_same_safe_diagnostic(tmp_path, caplog):
    library = Library(SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'decomp.db')))
    with pytest.raises(sqlite3.OperationalError) as caught:
        with library.connect() as db:
            db.execute('ATTACH DATABASE ? AS secret', (str(tmp_path / 'absent' / 'db'),))
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
    assert len(messages(caplog)) == 1
    assert 'scope=match-library operation=execute' in messages(caplog)[0]
    assert library.summary()['functions'] == 0


def test_diagnostics_are_rate_bounded_but_never_skip_the_failure(tmp_path, caplog, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: clock[0])
    for i in range(100):
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect(tmp_path / str(i) / 'db', factory=Connection)
    assert len(messages(caplog)) == 1
    assert len(diagnostics._last) == 1  # paths and exception text never grow the registry
    clock[0] += 60
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(tmp_path / 'still-missing' / 'db', factory=Connection)
    assert len(messages(caplog)) == 2


def test_non_storage_error_and_missing_optional_metadata_keep_original(tmp_path, caplog, monkeypatch):
    db = sqlite3.connect(':memory:', factory=Connection)
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        db.execute('SELECT * FROM secret_table')
    assert not messages(caplog)
    monkeypatch.setattr(diagnostics.shutil, 'disk_usage', lambda _: (_ for _ in ()).throw(OSError('secret')))
    with pytest.raises(sqlite3.OperationalError) as caught:
        db.execute('ATTACH DATABASE ? AS secret', (str(tmp_path / 'missing' / 'db'),))
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
    assert 'secret' not in '\n'.join(messages(caplog))
    db.close()


@pytest.mark.parametrize("code, category", [(sqlite3.SQLITE_IOERR | (1 << 8), 'IOERR'),
                                           (sqlite3.SQLITE_FULL, 'FULL')])
def test_extended_codes_and_unavailable_platform_metadata_are_safe(tmp_path, caplog, monkeypatch, code, category):
    import sys
    monkeypatch.setitem(sys.modules, 'resource', None)  # resource is unavailable on Windows
    monkeypatch.setattr(diagnostics, '_descriptors', lambda: -1)
    error = sqlite3.OperationalError('secret SQL and credential')
    error.sqlite_errorcode = code
    diagnostics.report(error, (tmp_path / 'private.sqlite3').as_uri() + '?token=secret', 'secret operation')
    text = messages(caplog)[0]
    assert f'code={code} category={category}' in text
    assert 'operation=other' in text and 'open_fds=-1 fd_limit=-1' in text
    assert not any(value in text for value in ('secret', 'private.sqlite3', str(tmp_path)))
