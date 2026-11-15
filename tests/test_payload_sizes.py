"""Storage totals must never scan blob pages on a settings request."""
import sqlite3

import pytest

from decompnet import payload_sizes
from decompnet.app import database_storage
from decompnet.network import Network


class Log:
    def check_cancel(self):pass
    def progress(self,event):pass


def test_sizes_follow_other_writers_ignore_replace_update_delete_and_rollback(tmp_path):
    net=Network(tmp_path/'data.db');store=net.store
    with sqlite3.connect(store.path) as other:
        other.execute('INSERT INTO text_payloads VALUES(?,?,?)',('a',b'one',15))
        other.execute('INSERT OR IGNORE INTO text_payloads VALUES(?,?,?)',('a',b'ignored',99))
    assert payload_sizes.stats(store)==dict(contents=1,logical_bytes=15,stored_bytes=3,complete=True)
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO text_payloads VALUES(?,?,?)',('a',b'two!!',20))
        identity=db.execute("SELECT rowid FROM text_payloads WHERE hash='a'").fetchone()[0]
        db.execute('INSERT OR REPLACE INTO text_payloads(rowid,hash,payload,raw_bytes) VALUES(?,?,?,?)',(identity,'b',b'next',30))
        db.execute("UPDATE text_payloads SET rowid=rowid+10,hash='c',payload=X'FFFF',raw_bytes=40 WHERE hash='b'")
    assert payload_sizes.stats(store)==dict(contents=1,logical_bytes=40,stored_bytes=2,complete=True)
    with pytest.raises(RuntimeError):
        with store.tx() as db:
            db.execute('DELETE FROM text_payloads')
            raise RuntimeError('rollback')
    assert payload_sizes.stats(store)['logical_bytes']==40
    with store.tx() as db:db.execute('DELETE FROM text_payloads')
    assert payload_sizes.stats(store)==dict(contents=0,logical_bytes=0,stored_bytes=0,complete=True)


def test_legacy_inventory_is_explicit_bounded_cancellable_and_resumable(tmp_path,monkeypatch):
    net=Network(tmp_path/'data.db');store=net.store
    with store.tx() as db:
        for suffix in ('insert','update','delete'):db.execute('DROP TRIGGER payload_sizes_'+suffix)
        db.executemany('INSERT INTO text_payloads VALUES(?,?,?)',[(str(i),b'x'*100,200) for i in range(7)])
        payload_sizes.install(db)
    # Reopening/installing schema must not scan or backfill legacy content.
    assert payload_sizes.stats(store)==dict(contents=0,logical_bytes=0,stored_bytes=0,complete=False)
    monkeypatch.setattr(payload_sizes,'BATCH_BYTES',250)
    import decompnet.app.maintenance as maintenance
    calls=[]
    def cancel():
        calls.append(1)
        raise RuntimeError('cancel after committed batch')
    monkeypatch.setattr(maintenance,'checkpoint',cancel)
    with pytest.raises(RuntimeError,match='cancel'):payload_sizes.backfill(store,Log())
    assert payload_sizes.stats(store)['contents']==2
    # Another writer changes records behind the cursor while maintenance is stopped.
    with sqlite3.connect(store.path) as other:
        other.execute("UPDATE text_payloads SET raw_bytes=300 WHERE hash='0'")
        other.execute("DELETE FROM text_payloads WHERE hash='1'")
        other.execute('INSERT INTO text_payloads(rowid,hash,payload,raw_bytes) VALUES(-5,?,?,?)',('new',b'n',10))
    monkeypatch.setattr(maintenance,'checkpoint',lambda:None)
    payload_sizes.backfill(store,Log());payload_sizes.backfill(store,Log())
    assert payload_sizes.stats(store)==dict(contents=7,logical_bytes=1310,stored_bytes=601,complete=True)


def test_settings_stats_cannot_read_blob_or_trailing_raw_size_columns(tmp_path):
    net=Network(tmp_path/'data.db');store=net.store
    with store.tx() as db:db.execute('INSERT INTO text_payloads VALUES(?,?,?)',('payload',b'x'*100000,200000))
    def authorize(action,table,column,*_):
        if action==sqlite3.SQLITE_READ and table=='text_payloads' and column in ('payload','raw_bytes'):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    reader=store._reader();reader.set_authorizer(authorize)
    try:
        result=database_storage.stats(net)
        assert result['statistics_complete'] and result['logical_bytes']==200000
        assert result['stored_bytes']==100000
    finally:reader.set_authorizer(None)
