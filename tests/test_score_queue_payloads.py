"""Improvement queue metadata must not inflate saved C until a caller requests it."""
from types import SimpleNamespace

from decompnet import payload_storage
from decompnet.app import score_commits
from decompnet.store import Store


def test_score_queue_preserves_filters_without_decoding_sources(tmp_path):
    store = Store(tmp_path/'db')
    packed = 'void candidate(void) { /* '+('source '*200)+' */ }'
    rows = [(1,1,'packed','open',packed,5,10),
            (2,1,'inline','open','void inline_source(void) {}',4,None),
            (3,1,'empty','open','',4,None),
            (4,1,'missing','open',None,4,None),
            (5,1,'matched','matched',packed,0,None),
            (6,1,'unscored','open',packed,-1,None),
            (7,1,'equal','open',packed,5,5),
            (8,1,'worse','open',packed,6,5),
            (9,2,'other_project','open',packed,5,None)]
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}'),(2,'other','','{}')")
        for fid,pid,name,status,source,score,baseline in rows:
            db.execute('INSERT INTO functions(id,project_id,name,status,best_source,best_score) VALUES(?,?,?,?,?,?)',
                       (fid,pid,name,status,source,score))
            if baseline is not None:
                db.execute('INSERT INTO score_commit_baselines VALUES(?,?,?)', (pid,fid,baseline))
    assert store.one('SELECT typeof(best_source) kind FROM main.functions WHERE id=1')['kind'] == 'blob'
    calls = []
    def read(digest, data):
        calls.append(digest)
        return payload_storage.unpack(digest, data)
    for conn in (store.conn, store._reader()):
        conn.create_function('payload_read', 2, read, deterministic=True)
    net = SimpleNamespace(project_store=lambda _:store,
                          cfg=lambda _: {'score_commits':{'enabled':True}})
    state = score_commits.status(net, {'id':1})
    assert state['pending'] == 2
    assert state['items'] == [{'name':'inline','before':None,'score':4},
                              {'name':'packed','before':10,'score':5}]
    assert {r['name'] for r in store.all(score_commits.PENDING, 1)} == {'inline','packed'}
    assert calls == []
    # This optimization must not replace the detail view used by real source reads.
    assert store.one('SELECT best_source FROM functions WHERE id=1')['best_source'] == packed
    assert calls
    store.conn.close()
