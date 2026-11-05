"""Synthetic migrations preserve exact historical bytes, revisions and reference-safe cleanup."""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from decompnet import header_storage
from decompnet.app import header_cleanup
from decompnet.store import Store


def insert(db, project, version, header, unit=''):
    db.execute('INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) VALUES(?,?,?,?,?,?,?)',
               (project,version,header,7,'saved change',version,unit))


def test_legacy_migration_deduplicates_compresses_and_preserves_exact_versions(tmp_path):
    path=tmp_path/'old.db';conn=sqlite3.connect(path)
    conn.execute('CREATE TABLE context_versions(project_id INTEGER,version INTEGER,header TEXT,agent_id INTEGER,message TEXT,created_at REAL,unit TEXT DEFAULT \'\',PRIMARY KEY(project_id,version))')
    text='/* ☃ */\r\n\x00typedef struct { int member; } Item;\n'*1000
    for project,version in [(1,1),(1,2),(2,1)]:insert(conn,project,version,text)
    insert(conn,1,3,None);insert(conn,1,4,'','unit.c');conn.commit();conn.close()
    store=Store(path)
    rows=store.all('SELECT * FROM context_versions ORDER BY project_id,version')
    assert rows[0]['header']==rows[1]['header']==rows[4]['header']==text
    assert rows[0]['agent_id']==7 and rows[0]['message']=='saved change'
    assert rows[2]['header'] is None and rows[3]['header']=='' and rows[3]['unit']=='unit.c'
    stats=header_storage.stats(store)
    assert stats['versions']==5 and stats['contents']==2
    assert stats['stored_bytes']<stats['logical_bytes']/10
    assert store.one('PRAGMA foreign_key_check') is None
    reopened=Store(path)
    assert reopened.one('SELECT header FROM context_versions WHERE project_id=1 AND version=1')['header']==text


def test_failed_migration_rolls_back_original_table(tmp_path,monkeypatch):
    path=tmp_path/'old.db';conn=sqlite3.connect(path)
    conn.execute('CREATE TABLE context_versions(project_id INTEGER,version INTEGER,header TEXT,agent_id INTEGER,message TEXT,created_at REAL,unit TEXT,PRIMARY KEY(project_id,version))')
    insert(conn,1,1,'first');insert(conn,1,2,'second');conn.commit();conn.close()
    pack=header_storage.Codec.pack
    def interrupted(text):
        if text=='second':raise RuntimeError('interrupted migration')
        return pack(text)
    monkeypatch.setattr(header_storage.Codec,'pack',staticmethod(interrupted))
    with pytest.raises(RuntimeError,match='interrupted migration'):Store(path)
    conn=sqlite3.connect(path)
    assert conn.execute('SELECT header FROM context_versions ORDER BY version').fetchall()==[('first',),('second',)]
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_history'").fetchone()
    conn.close()


def test_view_writes_rollback_thread_reads_and_cache_invalidation(tmp_path):
    store=Store(tmp_path/'app.db')
    with store.tx() as db:insert(db,1,1,'before')
    first=store.read_version('scope_context',1)
    assert store.one('SELECT header FROM context_versions')['header']=='before'
    with store.tx() as db:db.execute('UPDATE context_versions SET header=? WHERE version=1',('after',))
    assert store.read_version('scope_context',1)>first
    result=[]
    thread=threading.Thread(target=lambda:result.append(store.one('SELECT header FROM context_versions')['header']))
    thread.start();thread.join();assert result==['after']
    count=store.one('SELECT COUNT(*) n FROM header_blobs')['n']
    with pytest.raises(RuntimeError):
        with store.tx() as db:
            insert(db,1,2,'rolled back')
            raise RuntimeError()
    assert store.one('SELECT COUNT(*) n FROM header_blobs')['n']==count
    assert store.one('SELECT MAX(version) v FROM context_versions')['v']==1
    with store.tx() as db:db.execute('DELETE FROM context_versions WHERE project_id=1')
    assert not store.all('SELECT * FROM context_versions')


def test_metadata_queries_do_not_decompress_or_pin_and_corruption_fails_closed(tmp_path):
    store=Store(tmp_path/'app.db')
    with store.tx() as db:insert(db,1,1,'header')
    assert tuple(store.one('SELECT unit,version FROM context_versions'))==('',1)
    assert not store.header_codec.pins
    with store.tx() as db:db.execute("UPDATE header_blobs SET payload=x'0000'")
    with pytest.raises(sqlite3.DatabaseError):store.one('SELECT header FROM context_versions')


class Log:
    def __init__(self):self.cancel_event=threading.Event();self.progresses=[];self.lines=[]
    def __call__(self,line):self.lines.append(line)
    def check_cancel(self):
        if self.cancel_event.is_set():raise RuntimeError('cancelled')
    def progress(self,data):self.progresses.append(data)


def fixture(tmp_path):
    store=Store(tmp_path/'app.db')
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'demo','/tmp','{}')")
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(2,'other','/tmp','{}')")
        db.execute("INSERT INTO functions(id,project_id,name) VALUES(1,1,'sample')")
        for version in range(1,11):insert(db,1,version,'header '+str(version),'other.c' if version==10 else '')
        insert(db,2,1,'undo-old');insert(db,2,2,'undo-current')
        db.execute('INSERT INTO attempts(function_id,context_version) VALUES(1,1)')
        db.execute('CREATE TABLE repair_examples(id INTEGER PRIMARY KEY,project_id INTEGER,evidence TEXT)')
        evidence={stage:{'context_version':version} for stage,version in [('before',2),('after',3),('previous',4)]}
        db.execute('INSERT INTO repair_examples VALUES(1,1,?)',(json.dumps(evidence),))
        db.execute("INSERT INTO settings VALUES('boundary-repair:2:1','{}')")
    store._track_reads('repair_examples','repair_profiles','evidence,project_id')
    return SimpleNamespace(store=store)


def test_pruning_keeps_attempt_repair_latest_pinned_and_undo_versions(tmp_path):
    net=fixture(tmp_path);store=net.store
    assert store.one('SELECT header FROM context_versions WHERE project_id=1 AND version=5')['header']=='header 5'
    stats,candidates=header_cleanup.summary(net)
    assert candidates==[(1,6),(1,7),(1,8)]
    assert stats['unused_versions']==3
    result=header_cleanup.prune(net,Log())
    assert result['removed_versions']==3
    assert {r['version'] for r in store.all('SELECT version FROM context_versions WHERE project_id=1')}=={1,2,3,4,5,9,10}
    assert store.one('SELECT COUNT(*) n FROM context_versions WHERE project_id=2')['n']==2
    assert store.one('PRAGMA foreign_key_check') is None
    assert store.one('SELECT COUNT(*) n FROM header_blobs')['n']==9


def test_pruning_rechecks_new_attempts_and_can_resume_cancelled_batches(tmp_path,monkeypatch):
    net=fixture(tmp_path);store=net.store;original=header_cleanup.summary
    def preview_then_reference(net):
        result=original(net)
        with store.tx() as db:db.execute('INSERT INTO attempts(function_id,context_version) VALUES(1,6)')
        return result
    monkeypatch.setattr(header_cleanup,'summary',preview_then_reference)
    monkeypatch.setattr(header_cleanup,'BATCH',1)
    log=Log()
    def progress(data):log.cancel_event.set()
    log.progress=progress
    with pytest.raises(RuntimeError,match='cancelled'):header_cleanup.prune(net,log)
    assert store.one('SELECT version FROM context_versions WHERE project_id=1 AND version=6')
    monkeypatch.setattr(header_cleanup,'summary',original)
    assert header_cleanup.prune(net,Log())['removed_versions']>0


def test_changed_repair_references_abort_stale_pruning(tmp_path,monkeypatch):
    net=fixture(tmp_path);original=header_cleanup.summary
    def changed(net):
        result=original(net)
        with net.store.tx() as db:db.execute('UPDATE repair_examples SET evidence=?',(json.dumps({'after':{'context_version':6}}),))
        return result
    monkeypatch.setattr(header_cleanup,'summary',changed)
    with pytest.raises(ValueError,match='Repair evidence changed'):header_cleanup.prune(net,Log())
    assert net.store.one('SELECT COUNT(*) n FROM context_versions')['n']==12


def test_compaction_reclaims_deleted_pages_and_preserves_headers(tmp_path):
    net=fixture(tmp_path)
    with net.store.tx() as db:
        db.execute('CREATE TABLE scratch(payload BLOB)')
        db.execute('INSERT INTO scratch VALUES(randomblob(2000000))')
    net.store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    with net.store.tx() as db:db.execute('DELETE FROM scratch')
    net.store.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    result=header_cleanup.compact(net,Log())
    assert result['reclaimed_bytes']>1000000
    assert net.store.one('SELECT header FROM context_versions WHERE project_id=1 AND version=1')['header']=='header 1'
    assert net.store.one('PRAGMA integrity_check')[0]=='ok'


def test_compaction_cancellation_rolls_back_and_allows_future_writes(tmp_path):
    net=fixture(tmp_path)
    with net.store.tx() as db:
        db.execute('CREATE TABLE scratch(payload BLOB)')
        db.executemany('INSERT INTO scratch VALUES(randomblob(500))',[()]*1000)
    log=Log()
    class Cancel:
        calls=0
        def is_set(self):
            self.calls+=1
            return self.calls>3
    log.cancel_event=Cancel()
    with pytest.raises(RuntimeError,match='cancelled'):header_cleanup.compact(net,log)
    assert net.store.one('SELECT COUNT(*) n FROM scratch')['n']==1000
    with net.store.tx() as db:insert(db,1,11,'still writable')
    assert net.store.one('PRAGMA integrity_check')[0]=='ok'


def test_unknown_repair_references_preserve_the_project_history(tmp_path):
    net=fixture(tmp_path)
    with net.store.tx() as db:db.execute("UPDATE repair_examples SET evidence='legacy invalid JSON'")
    assert header_cleanup.summary(net)[0]['unused_versions']==0
    assert header_cleanup.prune(net,Log())['removed_versions']==0
    assert net.store.one('SELECT COUNT(*) n FROM context_versions')['n']==12


def test_distinct_maintenance_actions_do_not_reuse_the_wrong_job():
    jobs=SimpleNamespace(promote=lambda _:True,_lock=threading.RLock(),jobs={'existing':{'id':'existing','kind':'C header cleanup','state':'running'}})
    assert header_cleanup.start(None,jobs,'headers')=='existing'
    with pytest.raises(ValueError,match='Another database maintenance task'):
        header_cleanup.start(None,jobs,'compact')


@pytest.mark.parametrize('cancel',[False,True])
def test_cleanup_waits_for_other_sqlite_writer_and_can_cancel(tmp_path,cancel):
    net=fixture(tmp_path);log=Log();waiting=threading.Event();finished=threading.Event();errors=[]
    count=net.store.one('SELECT COUNT(*) n FROM context_history')['n']
    writer=sqlite3.connect(net.store.path,isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    original=log.progress
    def progress(value):
        original(value)
        if value['phase'].startswith('Waiting for database writer'):waiting.set()
    log.progress=progress
    def work():
        try:header_cleanup.prune(net,log)
        except Exception as error:errors.append(error)
        finally:finished.set()
    worker=threading.Thread(target=work)
    try:
        worker.start()
        assert waiting.wait(3),'Cleanup did not report SQLite lock contention'
        assert not finished.is_set()
        if cancel:log.cancel_event.set()
        else:writer.execute('COMMIT')
        assert finished.wait(3),'Cleanup did not resume or cancel promptly'
    finally:
        if writer.in_transaction:writer.execute('ROLLBACK')
        writer.close();worker.join(3)
    assert net.store.conn.execute('PRAGMA busy_timeout').fetchone()[0]==5000
    if cancel:
        assert len(errors)==1 and str(errors[0])=='cancelled'
        assert net.store.one('SELECT COUNT(*) n FROM context_history')['n']==count
    else:
        assert not errors
        assert net.store.one('SELECT COUNT(*) n FROM context_history')['n']<count
    assert net.store.one('PRAGMA foreign_key_check') is None
