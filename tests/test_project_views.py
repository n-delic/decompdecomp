"""View optimizations must agree with ordinary progress accounting after every write."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.view_snapshots import Snapshots, map_payload
from decompnet.network import IN_BUILD, PROGRESS_COLUMNS
from decompnet.store import Store
from tests.test_loading import tiny_game


def assert_totals(store, pid):
    expected = store.one(f'SELECT {PROGRESS_COLUMNS},SUM(attempts) AS attempts FROM functions WHERE project_id=? AND {IN_BUILD}', pid)
    actual = store.one('SELECT * FROM project_totals WHERE project_id=?', pid)
    for key in expected.keys():
        assert (actual[key] if actual else 0) == (expected[key] or 0), key


def test_rollups_follow_import_edits_moves_deletions_and_rollback(tmp_path):
    store = Store(tmp_path/'db')
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'a','','{}'),(2,'b','','{}')")
        for i in range(100):
            db.execute('INSERT INTO functions(project_id,name,size,status,attempts,meta) VALUES(?,?,?,?,?,?)',
                       (1,str(i),i,'open',i,'{}'))
    edits = ["UPDATE functions SET status='matched' WHERE id%2=0",
             "UPDATE functions SET meta='{\"set_aside\":\"sdk\"}' WHERE id%3=0",
             "UPDATE functions SET meta='{\"gone\":true}' WHERE id%5=0",
             "UPDATE functions SET status='asm',size=NULL WHERE id%7=0",
             "UPDATE functions SET project_id=2 WHERE id%4=0",
             "DELETE FROM functions WHERE id%9=0"]
    for sql in edits:
        with store.tx() as db:
            db.execute(sql)
        assert_totals(store,1); assert_totals(store,2)
    revision = store.one('SELECT map_revision FROM project_totals WHERE project_id=1')[0]
    with pytest.raises(RuntimeError), store.tx() as db:
        db.execute("UPDATE functions SET status='matched'")
        raise RuntimeError('rollback')
    assert store.one('SELECT map_revision FROM project_totals WHERE project_id=1')[0] == revision
    assert_totals(store,1); assert_totals(Store(tmp_path/'db'),2)


def test_upgrade_builds_rollups_for_existing_functions(tmp_path):
    path=tmp_path/'db'
    store=Store(path)
    with store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size) VALUES(1,'a',100)")
    for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'view_%'").fetchall():
        store.conn.execute(f'DROP TRIGGER {row[0]}')
    store.conn.execute('DROP TABLE project_totals')
    store.conn.close()
    assert_totals(Store(path),1)


def test_sidebar_batches_projects_without_scanning_functions(tmp_path, monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path)
    tiny_game(net)
    asked=[]
    read=net.store._read
    monkeypatch.setattr(net.store,'_read',lambda sql,args,single:asked.append(sql) or read(sql,args,single))
    with net.summary_batch() as projects:
        values=[net.summary(row['slug']) for row in projects]
    assert values and len(asked)==1
    assert 'FROM functions' not in asked[0]


def test_map_snapshots_and_delta_keep_structural_edits_as_full_maps(tmp_path, monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path)
    tiny_game(net)
    # Use small synthetic map rows while retaining real revision triggers.
    rows=[dict(name=str(i),vram=i,unit='',size=4,best=100,status='open') for i in range(10)]
    calls=[]
    monkeypatch.setattr(net,'function_map',lambda slug:calls.append(slug) or [dict(r) for r in rows])
    first=map_payload(net,'game')
    assert map_payload(net,'game')==first and calls==['game']
    rows[3]['best']=50
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_score=50 WHERE project_id=1')
    changed=map_payload(net,'game',first['sig'],delta=True)
    assert len(changed['delta'])==1 and changed['delta'][0][0]==3
    assert map_payload(net,'game',changed['sig'],delta=True)['same']
    rows[3]['name']='renamed'
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='renamed' WHERE id=(SELECT MIN(id) FROM functions)")
    assert 'rows' in map_payload(net,'game',changed['sig'],delta=True)


def test_snapshot_cache_coalesces_and_evicts_by_row_budget():
    import threading
    cache=Snapshots(limit=3)
    started=threading.Event(); release=threading.Event(); calls=[]
    def build():
        calls.append(1); started.set(); assert release.wait(3)
        return [1,2]
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(cache.get,'a',build,len)
        assert started.wait(3)
        second=pool.submit(cache.get,'a',build,len)
        release.set()
        assert first.result()==second.result()==[1,2]
    assert calls==[1]
    cache.get('b',lambda:[3,4],len)
    assert 'a' not in cache.ready


def test_full_map_refresh_does_not_reuse_old_attempt_or_claim_counts(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path)
    tiny_game(net)
    calls=[]
    def rows(slug):
        calls.append(1)
        return [dict(name='f',vram=1,unit='',size=4,attempts=net.store.one('SELECT SUM(attempts) FROM functions')[0])]
    monkeypatch.setattr(net,'function_map',rows)
    first=map_payload(net,'game')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET attempts=attempts+1')
    fresh=map_payload(net,'game')
    assert fresh['sig']==first['sig']  # Ordinary polling still relies on incremental live attempts.
    assert fresh['rows']!=first['rows'] and len(calls)==2


def test_lightweight_requests_have_capacity_while_diff_readers_are_blocked(tmp_path,monkeypatch):
    import asyncio
    import threading

    from decompnet.app import server
    from decompnet.app.routes import state
    net=RomNetwork(tmp_path/'db',tmp_path)
    captured=[]
    register=state.register
    def capture(app):
        captured.append(app)
        register(app)
    monkeypatch.setattr(state,'register',capture)
    home=tmp_path/'.decomp';home.mkdir()
    _,manager,_,_=server.build(net,home,6499,'test-secret')
    api=captured[0]
    ready=threading.Event();release=threading.Event();lock=threading.Lock();entered=[]
    def slow():
        with lock:
            entered.append(1)
            if len(entered)==4:ready.set()
        assert release.wait(5)
    async def check():
        tasks=[asyncio.create_task(api.ui_call(slow)) for _ in range(4)]
        try:
            assert await asyncio.to_thread(ready.wait,3)
            assert await asyncio.wait_for(api.fast_call(lambda:42),3)==42
        finally:
            release.set()
            await asyncio.gather(*tasks)
    try:asyncio.run(check())
    finally:manager.shutdown()
