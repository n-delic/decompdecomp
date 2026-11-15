"""Short-lived worker threads must not accumulate database descriptors until cyclic GC."""
import gc
import sqlite3
import threading

from decompnet.store import Store


def test_worker_threads_share_pooled_readers_without_gc(tmp_path,monkeypatch):
    store=Store(tmp_path/'test.db');closed=[];opened=[];errors=[]
    connect=sqlite3.connect
    from decompnet.payload_storage import Connection as PayloadConnection
    class Connection(PayloadConnection):
        def __init__(self,*args,**kwargs):
            opened.append(1)
            super().__init__(*args,**kwargs)
        def close(self):
            closed.append(threading.get_ident())
            super().close()
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**kw:connect(*a,**(kw | {'factory':Connection})))
    def read():
        try:
            assert store.one('SELECT 1 AS value')['value']==1
            assert store.all('SELECT 2 AS value')[0]['value']==2
        except BaseException as error:errors.append(error)
    enabled=gc.isenabled();gc.disable()
    try:
        for _ in range(20):
            worker=threading.Thread(target=read);worker.start();worker.join()
        assert not errors
        # 20 threads came and went: one reading connection served them all, and it stays open for the next.
        assert len(opened)==1 and closed==[] and len(store._readers)==1
        assert store.one('SELECT 3 AS value')['value']==3
    finally:
        if enabled:gc.enable()
        store.close_readers()
        assert closed
        store.conn.close()
