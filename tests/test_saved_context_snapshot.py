"""Bulk saved headers retain ordinary latest-version, fallback and checksum behavior."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.network import Network
from tests import test_progress, test_project_storage

project = test_progress.project
scoped = test_project_storage.scoped


def put(store,pid,version,unit,header):
    with store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,version,unit,header) VALUES(?,?,?,?)',
                   (pid,version,unit,header))


def test_bulk_latest_headers_use_project_store_fallback_and_only_pin_selected_versions(scoped,monkeypatch):
    net,stores=scoped
    store=stores[1]
    put(store,1,2,'','new common')
    put(store,1,3,'part','old part')
    put(store,1,4,'part','new part')
    put(store,1,5,'unrequested','unused')
    with store.header_codec.lock:store.header_codec.pins.clear()
    reads=[];original=store.all
    def read(sql,*args):
        reads.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(store,'all',read)
    result=net._contexts(1,['','part','missing',None])
    assert result['']['header']=='new common'
    assert result['part']['header']=='new part'
    assert result['missing']['header']=='new common'
    assert store.header_codec.pins=={(1,2),(1,4)}
    assert len(reads)==1
    assert net._contexts(1,[])=={}
    assert len(reads)==1
    assert net._contexts(2,['missing'])['missing']['header']=='int second;'
    with store.tx() as db:db.execute("DELETE FROM context_versions WHERE project_id=1 AND unit='' ")
    assert net._contexts(1,['missing'])=={'missing':None}


def test_bulk_headers_still_validate_selected_content_but_not_obsolete_content(tmp_path):
    net=Network(tmp_path/'db')
    try:
        with net.store.tx() as db:
            db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
        put(net.store,1,1,'','obsolete')
        put(net.store,1,2,'','current')
        with net.store.tx() as db:
            db.execute("UPDATE header_blobs SET payload=X'00' WHERE hash=(SELECT header_hash FROM context_history WHERE version=1)")
        assert net._contexts(1,[''])['']['header']=='current'
        put(net.store,1,3,'part','part header')
        with net.store.tx() as db:
            db.execute("UPDATE header_blobs SET payload=X'00' WHERE hash=(SELECT header_hash FROM context_history WHERE version=2)")
        assert net._contexts(1,['part'])['part']['header']=='part header'
        with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
            net._contexts(1,[''])
    finally:
        net._pool.shutdown();net.store.conn.close()


def test_bulk_headers_are_one_snapshot_across_a_concurrent_edit(tmp_path):
    net=Network(tmp_path/'db')
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'synthetic','','{}')")
    put(net.store,1,1,'a','old a');put(net.store,1,2,'b','old b')
    entered=threading.Event();release=threading.Event()
    def read():
        conn=net.store._reader()
        def decode(*args):
            entered.set()
            assert release.wait(3)
            return net.store.header_codec.read(*args)
        conn.create_function('header_read',4,decode)
        return net._contexts(1,['a','b'])
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(read)
            try:
                assert entered.wait(2)
                put(net.store,1,3,'a','new a');put(net.store,1,4,'b','new b')
            finally:release.set()
            assert {k:r['header'] for k,r in future.result(timeout=3).items()}=={'a':'old a','b':'old b'}
        assert {k:r['header'] for k,r in net._contexts(1,['a','b']).items()}=={'a':'new a','b':'new b'}
    finally:
        release.set();net._pool.shutdown();net.store.conn.close()


def test_full_and_header_partial_exports_do_not_query_each_unit(project,monkeypatch):
    from decompnet.app import saves
    net,folder,_=project
    pid=net.project_row('game')['id']
    for i in range(200):put(net.project_store(pid),pid,10+i,f'part{i}',f'typedef int Type{i};')
    original=net._contexts;calls=[]
    def batch(project_id,units):
        calls.append(1)
        return original(project_id,units)
    monkeypatch.setattr(net,'_contexts',batch)
    monkeypatch.setattr(net,'_context',lambda *a:pytest.fail('per-unit header query'))
    monkeypatch.setattr(saves,'_write_readme',lambda *a:None)
    with net.progress.view():
        full,_=saves._render(net,'game',folder)
        partial,_=saves._render_partial(net,net.project_row('game'),folder,[],headers=True)
    expected={key:value for key,value in full.items() if key.startswith('include/')}
    assert expected=={key:value for key,value in partial.items() if key.startswith('include/')}
    assert len(expected)==201 and len(calls)==2
    calls.clear()
    with net.progress.view():
        saves._render_partial(net,net.project_row('game'),folder,[],headers=False)
    assert calls==[]
