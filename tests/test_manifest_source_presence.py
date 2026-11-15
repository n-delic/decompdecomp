"""A partial save's manifest needs source presence, not the full project source text."""
from types import SimpleNamespace

from decompnet import payload_storage
from decompnet.app import saves
from decompnet.store import Store


def test_manifest_flags_agree_with_full_rows_without_decoding_sources(tmp_path):
    store = Store(tmp_path/'db')
    large = 'void synthetic(void) { /* '+('saved source '*100)+' */ }'
    values = [(1,'packed','matched',large,large), (2,'inline','open','short match','short best'),
              (3,'empty','open','',''), (4,'missing','open',None,None),
              (5,'mixed','matched','',large), (6,'other','open',large,large)]
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}'),(2,'other','','{}')")
        for address,name,status,matched,best in values:
            db.execute('INSERT INTO functions(project_id,name,vram,status,matched_source,best_source,best_score) VALUES(?,?,?,?,?,?,5)',
                       (2 if name=='other' else 1,name,address,status,matched,best))
    net = SimpleNamespace(project_store=lambda _:store)
    full = [dict(r) for r in saves._function_rows(net, 1, sources=True)]
    calls = []
    def read(digest, blob):
        calls.append(digest)
        return payload_storage.unpack(digest, blob)
    for conn in (store.conn, store._reader()):
        conn.create_function('payload_read',2,read,deterministic=True)
    metadata = [dict(r) for r in saves._function_rows(net, 1, sources=False)]
    assert metadata == [{k:v for k,v in r.items() if k not in {'matched_source','best_source'}} for r in full]
    assert [(r['name'],r['has_matched'],r['has_best']) for r in metadata] == [
        ('packed',1,1), ('inline',1,1), ('empty',0,0), ('missing',0,0), ('mixed',0,1)]
    assert calls == []
    # Full saves still resolve C and select exactly the same source files.
    assert [dict(r) for r in saves._function_rows(net, 1, sources=True)] == full
    assert calls
    assert saves._source_files(full) == {'src/packed.c':large+'\n',
                                        'best/inline.c':'short best\n','best/mixed.c':large+'\n'}
    store.conn.close()


def test_large_manifest_metadata_crosses_python_boundary_in_bounded_pages(tmp_path, monkeypatch):
    """CPU-bound Python workers must not contend for the GIL once per function."""
    import json
    store = Store(tmp_path/'pages.db')
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}'),(2,'other','','{}')")
        for i in range(1500):
            db.execute('INSERT INTO functions(project_id,name,unit,vram,size,status,best_score,meta) '
                       'VALUES(1,?,?,?,?,?,?,?)',
                       (f'f{i}', 'unit ü', 2**40+1500-i, None if i%3 else i,
                        'asm' if i%2 else 'open',None if i%5 else i,
                        json.dumps({'description':'quote " and newline\n ü','number':2**60+i})))
        db.execute("INSERT INTO functions(project_id,name,vram) VALUES(2,'private',0)")
    expected = [dict(row) for row in store.all(
        'SELECT name,unit,vram,size,status,matched_agent_id,best_score,best_agent_id,readable,meta,'
        '0 AS has_matched,0 AS has_best FROM main.functions WHERE project_id=1 ORDER BY vram')]
    transmitted = []
    original = store.all
    def read(sql, *params):
        rows = original(sql, *params)
        transmitted.append(len(rows))
        return rows
    monkeypatch.setattr(store,'all',read)
    net = SimpleNamespace(project_store=lambda _:store)
    try:
        assert [dict(row) for row in saves._function_rows(net,1,sources=False)] == expected
        assert len(transmitted) == 1  # One SQLite snapshot, no per-page drift.
        assert transmitted[0] < 32  # Old policy returns all 1,500 native rows.
        assert [dict(row) for row in saves._function_rows(net,1,sources=False,names=['f2','f8','private'])] == [
            row for row in expected if row['name'] in ('f2','f8')]
        assert saves._function_rows(net,1,sources=False,names=[]) == []
    finally:
        store.conn.close()
