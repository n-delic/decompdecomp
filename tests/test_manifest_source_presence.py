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
