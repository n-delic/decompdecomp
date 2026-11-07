"""Lease identity metadata stays current across processes and transaction rollback."""
import sqlite3

import pytest

from decompnet.network import Network
from tests.test_project_database_migration import close


def test_historical_permuters_are_not_reloaded_on_each_expiry_check(tmp_path,monkeypatch):
    net=Network(tmp_path/'catalog.db')
    try:
        with net.store.tx() as db:
            db.executemany('INSERT INTO agents(name,token_hash) VALUES(?,?)',[(f'permuter-{i}',str(i)) for i in range(2000)])
        seen=[];select=net.store.all
        def rows(sql,*args):
            result=select(sql,*args)
            if 'FROM agents' in sql:seen.append(len(result))
            return result
        monkeypatch.setattr(net.store,'all',rows)
        first=net._nonexclusive_agent_ids();assert len(first)==2000
        first.clear()
        for _ in range(5):assert len(net._nonexclusive_agent_ids())==2000
        assert seen==[2000]
    finally:close(net)


def test_external_identity_edits_insert_and_delete_invalidate_without_restart(tmp_path):
    net=Network(tmp_path/'catalog.db')
    try:
        assert net._nonexclusive_agent_ids()==set()
        with sqlite3.connect(net.store.path) as db:
            aid=db.execute("INSERT INTO agents(name,owner,token_hash) VALUES('owner.permuter-4','wrong','token')").lastrowid
        assert net._nonexclusive_agent_ids()==set()
        with sqlite3.connect(net.store.path) as db:db.execute("UPDATE agents SET owner='owner' WHERE id=?",(aid,))
        assert net._nonexclusive_agent_ids()=={aid}
        with sqlite3.connect(net.store.path) as db:db.execute("UPDATE agents SET name='ordinary-worker' WHERE id=?",(aid,))
        assert net._nonexclusive_agent_ids()==set()
        with sqlite3.connect(net.store.path) as db:db.execute("UPDATE agents SET name='permuter-8-lane-2' WHERE id=?",(aid,))
        assert net._nonexclusive_agent_ids()=={aid}
        with sqlite3.connect(net.store.path) as db:db.execute('DELETE FROM agents WHERE id=?',(aid,))
        assert net._nonexclusive_agent_ids()==set()
    finally:close(net)


def test_rollback_does_not_publish_speculative_identity_cache(tmp_path):
    net=Network(tmp_path/'catalog.db')
    try:
        assert net._nonexclusive_agent_ids()==set()
        with pytest.raises(RuntimeError,match='rollback'):
            with net.store.tx() as db:
                aid=db.execute("INSERT INTO agents(name,token_hash) VALUES('permuter-1','token')").lastrowid
                assert net._nonexclusive_agent_ids()=={aid}
                raise RuntimeError('rollback')
        assert net._nonexclusive_agent_ids()==set()
        before=net.store.read_version('lease_identities')
        with net.store.tx() as db:db.execute("INSERT INTO agents(name,token_hash) VALUES('permuter-1','token')")
        assert net.store.read_version('lease_identities')>before
        assert net._nonexclusive_agent_ids()=={aid}
    finally:close(net)
