"""Metadata-only progress imports preserve accounting without hydrating candidate C."""
from types import SimpleNamespace

from decompnet import match_bundle, payload_storage
from decompnet.app import saves
from decompnet.store import Store


def test_metadata_restore_reads_status_without_decoding_sources(tmp_path, monkeypatch):
    store = Store(tmp_path/'db')
    source = 'void synthetic(void) { /* '+('saved source '*100)+' */ }'
    statuses = [('matched','matched'), ('equal','open'), ('different','open'),
                ('empty','open'), ('open_src','open'), ('missing','matched')]
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}')")
        for address,(name,status) in enumerate(statuses, 100):
            db.execute('INSERT INTO functions(project_id,name,vram,status,best_source) VALUES(1,?,?,?,?)',
                       (name,address,status,None if name=='empty' else source))
    by_name = {r['name']:r for r in store.all('SELECT name,vram FROM functions')}
    saved = {name:{'vram':by_name[name]['vram'], **({'matched_by':'worker'} if name in
             {'matched','open_src','missing'} else {})} for name,_ in statuses}
    saved['unknown'] = {'vram':999,'matched_by':'worker'}
    files = {'src/matched.c':source, 'src/open_src.c':source, 'src/unknown.c':source,
             'best/equal.c':source, 'best/different.c':'different saved text', 'best/empty.c':''}
    evidence = []
    monkeypatch.setattr(match_bundle, 'restore_evidence_many',
                        lambda net,pid,names,manifest,check:evidence.extend(names))
    calls = []
    def read(digest, blob):
        calls.append(digest)
        return payload_storage.unpack(digest, blob)
    for conn in (store.conn, store._reader()):
        conn.create_function('payload_read',2,read,deterministic=True)
    before = [tuple(r) for r in store.all('SELECT id,status,best_source FROM main.functions')]
    net = SimpleNamespace(project_store=lambda _:store, _project=lambda _:{'id':1})
    updates = []
    report = lambda _:None
    report.progress = lambda **fields:updates.append(fields)
    result = saves._judge_saved(net, 'game', tmp_path, {}, by_name, saved, report,
                                files=files, judge_code=False)
    assert result == {'matched':1,'best':3,'lost':['missing','unknown']}
    assert set(evidence) == {(name,name) for name,_ in statuses if name!='missing'}
    assert updates[-1]['done'] == len(saved)
    assert calls == []
    assert [tuple(r) for r in store.all('SELECT id,status,best_source FROM main.functions')] == before
    assert store.one("SELECT best_source FROM functions WHERE name='equal'")['best_source'] == source
    assert calls  # Complete source reads still use the verified payload decoder.
    store.conn.close()
