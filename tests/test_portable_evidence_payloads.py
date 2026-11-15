"""Portable verification exports read shared captured text once in one SQL snapshot."""
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from decompnet import large_text_storage, match_bundle, payload_storage
from decompnet.store import Store


def test_portable_reads_distinct_text_and_preserves_records(tmp_path,monkeypatch):
    store = Store(tmp_path/'db')
    a,b = 'source a '*100, 'source b '*100
    x,y = 'header x '*100, 'header y '*100
    rows = [(1,1,a,x),(2,1,a,x),(3,1,b,x),(4,1,b,y),(5,1,'inline',''),
            (6,2,'other source '*100,'other header '*100)]
    expected = {'matches':{},'sources':{},'headers':{}}
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}'),(2,'other','','{}')")
        db.execute(match_bundle.SCHEMA)
        large_text_storage.install(db, ('verified_matches',))
        for fid,pid,source,header in rows:
            name = f'f{fid}'
            db.execute('INSERT INTO functions(id,project_id,name) VALUES(?,?,?)', (fid,pid,name))
            details = {'source_sha256':match_bundle.digest(source),
                       'header_sha256':match_bundle.digest(header) if header else None,
                       'attempt_id':fid,'mode':'project' if fid%2 else 'rom'}
            db.execute('INSERT INTO verified_matches VALUES(?,?,?,?,?)',
                       (fid,fid,json.dumps(details),source,header))
            if pid == 1:
                expected['matches'][name] = details
                expected['sources'][details['source_sha256']] = source
                if header:expected['headers'][details['header_sha256']] = header
    reads = Counter()
    unpack = payload_storage.unpack
    def read(digest, blob):
        reads[digest] += 1
        return unpack(digest, blob)
    monkeypatch.setattr(payload_storage,'unpack',read)
    for conn in (store.conn, store._reader()):
        conn.create_function('payload_read',2,read,deterministic=True)
    calls = []
    original = store.all
    def all_rows(sql, *args):
        calls.append(sql)
        return original(sql, *args)
    store.all = all_rows
    net = SimpleNamespace(project_store=lambda _:store)
    assert match_bundle.portable(net,1) == expected
    assert len(calls) == 1  # All records and text share one database read snapshot.
    assert reads == Counter({match_bundle.digest(text):1 for text in (a,b,x,y)})
    assert match_bundle.portable(net,3) == {}
    store.conn.close()


def test_packed_export_batches_transport_and_never_calls_sql_payload_decoder(tmp_path, monkeypatch):
    store=Store(tmp_path/'db')
    source,header='int synthetic(void){return 1;}\n'*100,'typedef int synthetic;\n'*100
    expected={}
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
        db.execute(match_bundle.SCHEMA);large_text_storage.install(db,('verified_matches',))
        for i in range(300):
            fid=2**53+i
            name=f'命名_{i}'
            details={'source_sha256':match_bundle.digest(source),'header_sha256':match_bundle.digest(header),
                     'attempt_id':2**60+i,'recorded_at':1794177170.1234567+i/1000,'note':'metadata '*100}
            expected[name]=details
            db.execute('INSERT INTO functions(id,project_id,name) VALUES(?,1,?)',(fid,name))
            db.execute('INSERT INTO verified_matches VALUES(?,?,?,?,?)',(fid,fid,json.dumps(details),source,header))
    def forbidden(*args):raise AssertionError('SQL decoded a verification text payload')
    for conn in (store.conn,store._reader()):conn.create_function('payload_read',2,forbidden)
    fetched=[];all_rows=store.all
    def counted(sql,*args):
        rows=all_rows(sql,*args);fetched.append(len(rows))
        # SQLite aggregates need not retain input order inside a page.
        return [{'packet':json.dumps(list(reversed(json.loads(row['packet']))))} for row in rows]
    monkeypatch.setattr(store,'all',counted)
    result=match_bundle.portable(SimpleNamespace(project_store=lambda _:store),1)
    assert result['matches']==expected
    assert list(result['matches'])==list(expected)
    assert result['sources']=={match_bundle.digest(source):source}
    assert result['headers']=={match_bundle.digest(header):header}
    assert fetched==[5]  # 300 records +302 distinct packed texts, <=128 per native result.
    store.conn.close()


@pytest.mark.parametrize('field',['details','source','header'])
@pytest.mark.parametrize('damage',['missing','checksum'])
def test_export_still_rejects_missing_or_corrupt_payloads(tmp_path,field,damage):
    import zlib
    store=Store(tmp_path/'db');source='source '*200;header='header '*200
    detail=json.dumps({'source_sha256':match_bundle.digest(source),'header_sha256':match_bundle.digest(header),'padding':'x'*1000})
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
        db.execute("INSERT INTO functions(id,project_id,name) VALUES(1,1,'synthetic')")
        db.execute(match_bundle.SCHEMA);large_text_storage.install(db,('verified_matches',))
        db.execute('INSERT INTO verified_matches VALUES(1,1,?,?,?)',(detail,source,header))
        digest=match_bundle.digest({'details':detail,'source':source,'header':header}[field])
        if damage=='missing':db.execute('DELETE FROM text_payloads WHERE hash=?',(digest,))
        else:db.execute('UPDATE text_payloads SET payload=? WHERE hash=?',(zlib.compress(b'incorrect text'),digest))
    with pytest.raises(ValueError,match='Missing database payload|checksum failed'):
        match_bundle.portable(SimpleNamespace(project_store=lambda _:store),1)
    store.conn.close()


def test_snapshot_carries_payloads_even_if_the_database_changes_after_fetch(tmp_path,monkeypatch):
    store=Store(tmp_path/'db');source='old source '*100;header='old header '*100
    detail={'source_sha256':match_bundle.digest(source),'header_sha256':match_bundle.digest(header)}
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
        db.execute("INSERT INTO functions(id,project_id,name) VALUES(1,1,'synthetic')")
        db.execute(match_bundle.SCHEMA);large_text_storage.install(db,('verified_matches',))
        db.execute('INSERT INTO verified_matches VALUES(1,1,?,?,?)',(json.dumps(detail),source,header))
    all_rows=store.all
    def changed(sql,*args):
        rows=all_rows(sql,*args)
        with store.tx() as db:
            db.execute("UPDATE verified_matches SET source='new source',header='new header'")
            db.execute('DELETE FROM text_payloads WHERE hash IN (?,?)',(match_bundle.digest(source),match_bundle.digest(header)))
        return rows
    monkeypatch.setattr(store,'all',changed)
    assert match_bundle.portable(SimpleNamespace(project_store=lambda _:store),1)=={
        'matches':{'synthetic':detail},'sources':{match_bundle.digest(source):source},'headers':{match_bundle.digest(header):header}}
    store.conn.close()
