"""Repair inputs are durable outside the operational DB; main references follow archive commits."""
import json
import sqlite3

import pytest

from decompnet.network import Network
from decompnet.repair_archive import Archive, archive_path, migrate
from decompnet.repair_feedback import RepairFeedback


def network(path):
    net=Network(path/'decomp.db')
    with net.store.tx() as db:
        db.execute("INSERT OR IGNORE INTO projects(id,slug,root,config) VALUES(1,'example',?,'{}')",(str(path),))
        db.execute("INSERT OR IGNORE INTO functions(id,project_id,name) VALUES(1,1,'example')")
    return net


def test_archive_deduplicates_large_inputs_and_exports_wal(tmp_path):
    archive=Archive(tmp_path/'repair-evidence.sqlite3')
    header='typedef int example;\n'*100
    one=archive.put('{"case":1}',{'header':header,'source':'first'})
    two=archive.put('{"case":2}',{'header':header,'source':'second'})
    assert one!=two and archive.put('{"case":1}',{'header':header,'source':'first'})==one
    assert json.loads(archive.read(one,snapshot=True))['header']==header
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM contents').fetchone()[0]==5
    out=tmp_path/'export.sqlite3';archive.export(out)
    exported=Archive(out)
    assert exported.read(two)==archive.read(two)
    assert exported.read(one,True)==archive.read(one,True)
    with archive.connection(write=True) as db:
        db.execute('UPDATE contents SET payload=? WHERE hash=(SELECT record_hash FROM cases WHERE hash=?)',(b'broken',one));db.commit()
    with pytest.raises(Exception):archive.read(one)


def test_new_cases_live_in_separate_db_and_main_rollback_is_safe(tmp_path):
    net=network(tmp_path)
    text=json.dumps({'function':'example','target_asm':'assembly'*1000,'after':{'source':'source'*1000}})
    with net.store.tx() as db:
        identity=db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',(text,)).lastrowid
    row=net.store.one('SELECT * FROM main.repair_examples WHERE id=?',identity)
    assert row['evidence_archive'] and row['evidence_hash'] is None
    assert len(row['evidence'])<len(text)//10
    assert net.store.one('SELECT evidence FROM repair_examples WHERE id=?',identity)['evidence']==text
    assert archive_path(net.store.path).is_file()
    with pytest.raises(RuntimeError),net.store.tx() as db:
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',(text,))
        raise RuntimeError('rolled back')
    assert net.store.one('SELECT count(*) n FROM repair_examples')['n']==1
    assert net.store.one('SELECT evidence FROM repair_examples')['evidence']==text
    net.store.conn.close()
    net=network(tmp_path)
    assert net.store.one('SELECT evidence FROM repair_examples')['evidence']==text


def test_legacy_migration_is_resumable_and_reads_remain_compatible(tmp_path):
    net=network(tmp_path)
    text=json.dumps({'function':'old','target_asm':'old assembly'*100})
    with net.store.tx() as db:
        db.execute('DROP TRIGGER pack_repair_examples_insert')
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',(text,))
    RepairFeedback(net)
    log=lambda *args:None
    log.check_cancel=lambda:None
    log.progress=lambda value:None
    assert migrate(net,log)==1
    assert migrate(net,log)==0
    assert net.store.one('SELECT evidence FROM repair_examples')['evidence']==text
    assert net.store.one('SELECT evidence_archive FROM main.repair_examples')['evidence_archive']
    with net.store.tx() as db:db.execute('UPDATE repair_examples SET evidence=?',(text+' ',))
    assert net.store.one('SELECT evidence FROM repair_examples')['evidence']==text+' '


def test_missing_archive_fails_loudly_and_never_reads_metadata_as_full_evidence(tmp_path):
    net=network(tmp_path)
    with net.store.tx() as db:db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',('{"after":{}}',))
    path=archive_path(net.store.path)
    path.rename(tmp_path/'moved.sqlite3')
    with pytest.raises(sqlite3.OperationalError):net.store.one('SELECT evidence FROM repair_examples')


def test_legacy_compressed_evidence_migrates_without_losing_full_payload(tmp_path):
    import hashlib
    import zlib

    from decompnet.payload_storage import metadata
    net=network(tmp_path)
    text=json.dumps({'function':'old','target_asm':'original assembly'*100})
    key=hashlib.sha256(text.encode()).hexdigest()
    with net.store.tx() as db:
        db.execute('DROP TRIGGER pack_repair_examples_insert')
        db.execute('INSERT INTO text_payloads VALUES(?,?,?)',(key,zlib.compress(text.encode()),len(text)))
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence,evidence_hash) VALUES(1,1,0,?,?)',(metadata(text),key))
    RepairFeedback(net)
    log=lambda *args:None
    log.check_cancel=lambda:None;log.progress=lambda *args:None
    assert migrate(net,log)==1
    assert net.store.one('SELECT evidence FROM repair_examples')['evidence']==text
    from decompnet.app.database_storage import collect
    collect(net)
    assert net.store.one('SELECT 1 FROM text_payloads WHERE hash=?',key) is None


def _write_archive(path,number):
    archive=Archive(path)
    for n in range(5):archive.put(json.dumps([number,n]),{'evidence':{'source':'shared'*200,'number':number,'n':n}})


def test_independent_processes_coordinate_archive_writes(tmp_path):
    import multiprocessing
    path=str(tmp_path/'shared.sqlite3')
    context=multiprocessing.get_context('spawn')
    processes=[context.Process(target=_write_archive,args=(path,n)) for n in range(2)]
    for process in processes:process.start()
    try:
        for process in processes:
            process.join(15)
            assert process.exitcode==0
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join()
    archive=Archive(path)
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM cases').fetchone()[0]==10
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_migration_cancellation_keeps_completed_case_and_resumes(tmp_path):
    net=network(tmp_path)
    with net.store.tx() as db:
        db.execute('DROP TRIGGER pack_repair_examples_insert')
        db.executemany('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',
                       [(json.dumps({'example':n}),) for n in range(2)])
    RepairFeedback(net)
    log=lambda *args:None
    stopped=[]
    def check():
        if stopped:raise RuntimeError('cancelled')
    log.check_cancel=check;log.progress=lambda value:stopped.append(value)
    with pytest.raises(RuntimeError,match='cancelled'):migrate(net,log)
    assert net.store.one('SELECT count(*) n FROM main.repair_examples WHERE evidence_archive IS NOT NULL')['n']==1
    log.check_cancel=lambda:None;log.progress=lambda value:None
    assert migrate(net,log)==1
    assert net.store.one('SELECT count(*) n FROM main.repair_examples WHERE evidence_archive IS NOT NULL')['n']==2


def test_compaction_includes_separate_archive_and_preserves_cases(tmp_path):
    import threading

    from decompnet.app.header_cleanup import compact
    net=network(tmp_path);archive=net.store.conn.repair_archive
    key=archive.put('{"example":1}',{'evidence':{'source':'saved'}})
    with archive.gate.hold(),archive.connection(write=True) as db:
        db.execute('CREATE TABLE scratch(value BLOB)')
        db.execute('INSERT INTO scratch VALUES(randomblob(2000000))');db.commit()
        db.execute('DELETE FROM scratch');db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    log=lambda *args:None
    log.check_cancel=lambda:None;log.progress=lambda value:None;log.cancel_event=threading.Event()
    assert compact(net,log)['reclaimed_bytes']>1000000
    assert archive.read(key)=='{"example":1}'


def test_database_alias_reads_the_same_archive(tmp_path):
    net=network(tmp_path)
    text='{"function":"alias","after":{}}'
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)',(text,))
    folder=tmp_path/'aliases';folder.mkdir()
    alias=folder/'alias.db';alias.symlink_to(net.store.path)
    other=Network(alias)
    assert other.store.one('SELECT evidence FROM repair_examples')['evidence']==text
