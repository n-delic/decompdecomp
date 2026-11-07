"""Portable verification exports read shared captured text once in one SQL snapshot."""
import json
from collections import Counter
from types import SimpleNamespace

from decompnet import large_text_storage, match_bundle, payload_storage
from decompnet.store import Store


def test_portable_reads_distinct_text_and_preserves_records(tmp_path):
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
    def read(digest, blob):
        reads[digest] += 1
        return payload_storage.unpack(digest, blob)
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
