"""Authentication stays read-only when advisory presence cannot acquire a writer."""
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from decompnet import network
from decompnet.network import Network, NetworkError
from decompnet.store import Store


@pytest.fixture
def identity(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(network, 'time', SimpleNamespace(time=lambda: clock[0]))
    net = Network.__new__(Network)
    net.store = Store(tmp_path / 'test.db')
    token = net.register_agent('synthetic-helper', 'owner')
    yield net, token, clock
    net.store.conn.close()


def test_presence_refresh_is_coalesced_but_identity_and_revocation_are_fresh(identity):
    net, token, clock = identity
    agent = net.authenticate(token)
    clock[0] += 4
    with net.store.tx() as db:
        db.execute('UPDATE agents SET trusted=0,owner=? WHERE id=?', ('new-owner', agent.id))
    statements = []
    net.store.conn.set_trace_callback(statements.append)
    current = net.authenticate(token)
    net.store.conn.set_trace_callback(None)
    assert not any(sql.startswith('BEGIN') for sql in statements)
    assert current.owner == 'new-owner' and not current.trusted
    assert net.store.one('SELECT last_seen FROM agents')['last_seen'] == 100
    clock[0] += 1
    net.authenticate(token)
    assert net.store.one('SELECT last_seen FROM agents')['last_seen'] == 105
    with net.store.tx() as db:
        db.execute('DELETE FROM agents WHERE id=?', (agent.id,))
    with pytest.raises(NetworkError, match='invalid API token'):
        net.authenticate(token)


def test_external_writer_does_not_fail_authentication_or_change_its_timeout(identity):
    net, token, clock = identity
    external = sqlite3.connect(net.store.path)
    net.store.conn.execute('PRAGMA busy_timeout=17')
    external.execute('BEGIN IMMEDIATE')
    try:
        agent = net.authenticate(token)
        assert agent.name == 'synthetic-helper'
        assert net.store.one('SELECT last_seen FROM agents')['last_seen'] is None
        assert net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == 17
    finally:
        external.rollback(); external.close()
    net.authenticate(token)
    assert net.store.one('SELECT last_seen FROM agents')['last_seen'] == clock[0]


def test_local_writer_does_not_queue_authentication(identity):
    net, token, _ = identity
    held = threading.Event(); release = threading.Event(); finished = threading.Event(); result = []
    def writer():
        with net.store.tx():
            held.set()
            assert release.wait(3)
    def reader():
        try:
            result.append(net.authenticate(token).name)
        finally:
            finished.set()
    writer_thread = threading.Thread(target=writer)
    writer_thread.start(); assert held.wait(1)
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        assert finished.wait(1), 'authentication queued behind an unrelated writer'
        assert result == ['synthetic-helper']
    finally:
        release.set(); writer_thread.join(); reader_thread.join()


def test_optional_transaction_rolls_back_body_errors_and_restores_timeout(identity):
    net, _, _ = identity
    before = net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0]
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        with net.store.try_tx() as db:
            assert db is not None
            db.execute("UPDATE agents SET owner='not committed'")
            # A body failure must never be mistaken for acquisition contention.
            error = sqlite3.OperationalError('database is locked')
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
    assert net.store.one('SELECT owner FROM agents')['owner'] == 'owner'
    assert net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == before


def test_optional_transaction_propagates_non_busy_acquisition_errors(identity, monkeypatch):
    net, _, _ = identity
    @contextmanager
    def broken():
        error = sqlite3.OperationalError('misuse')
        error.sqlite_errorcode = sqlite3.SQLITE_LOCKED
        raise error
        yield
    monkeypatch.setattr(net.store, 'tx', broken)
    before = net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0]
    with pytest.raises(sqlite3.OperationalError, match='misuse'):
        with net.store.try_tx():
            pytest.fail('non-BUSY acquisition error was swallowed')
    assert net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0] == before


def test_presence_recovers_after_wall_clock_moves_backwards(identity):
    net, token, clock = identity
    net.authenticate(token)
    clock[0] = 90
    net.authenticate(token)
    assert net.store.one('SELECT last_seen FROM agents')['last_seen'] == 90


def test_optional_transaction_commits_once_and_nested_writes_share_rollback(identity):
    net, _, _ = identity
    callbacks = []
    with net.store.try_tx() as db:
        db.execute("UPDATE agents SET owner='committed'")
        net.store.after_commit(lambda: callbacks.append('committed'))
    assert callbacks == ['committed']
    assert net.store.one('SELECT owner FROM agents')['owner'] == 'committed'
    with pytest.raises(ValueError, match='outer failed'):
        with net.store.tx():
            with net.store.try_tx() as db:
                db.execute("UPDATE agents SET owner='nested'")
            raise ValueError('outer failed')
    assert net.store.one('SELECT owner FROM agents')['owner'] == 'committed'
