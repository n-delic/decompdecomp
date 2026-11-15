"""Synthetic read-path benchmark; run with PYTHONPATH=src python tools/benchmark_project_views.py.

Uses a temporary database only. Timings describe these queries, not whole-app latency.
"""
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from decompnet.app.view_snapshots import cross_project_ids
from decompnet.network import IN_BUILD, PROGRESS_COLUMNS
from decompnet.store import Store

with tempfile.TemporaryDirectory() as root:
    store=Store(Path(root)/'db')
    with store.tx() as db:
        for p in range(1,13):
            db.execute('INSERT INTO projects(id,slug,root,config,arch,big_endian) VALUES(?,?,?,?,?,?)',(p,str(p),root,'{}','mips',1))
            db.executemany('INSERT INTO functions(project_id,name,size,n_insns,vram,status,best_score,target) VALUES(?,?,?,?,?,?,?,?)',
                          [(p,f'f{i}',128,32,i*128,'matched' if i%3==0 else 'open',i%100,(i%100).to_bytes(4,'little')*32) for i in range(2000)])
    def old():
        return [dict(store.one(f'SELECT {PROGRESS_COLUMNS} FROM functions WHERE project_id=? AND {IN_BUILD}',p)) for p in range(1,13)]
    def new():return [dict(r) for r in store.all('SELECT * FROM project_totals ORDER BY project_id')]
    def median(fn,n=40):
        fn();values=[]
        for _ in range(n):
            t=time.perf_counter();fn();values.append((time.perf_counter()-t)*1000)
        return round(statistics.median(values),3)
    a,b=old(),new()
    assert all(all((x[k] or 0)==y[k] for k in x) for x,y in zip(a,b))
    net=SimpleNamespace(store=store)
    proj={'arch':'mips','big_endian':1}
    t=time.perf_counter();cross_project_ids(net,proj);cold=(time.perf_counter()-t)*1000
    def updates():
        with store.tx() as db:
            for i in range(1,1001):
                db.execute('UPDATE functions SET attempts=attempts+1 WHERE id=?',(i,))
    write_with_rollups=median(updates,5)
    for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'view_%'").fetchall():
        store.conn.execute(f'DROP TRIGGER {row[0]}')
    write_without_rollups=median(updates,5)
    print(json.dumps({'synthetic_projects' :12,'functions':24000,'sidebar_old_ms':median(old),
                      'sidebar_rollups_ms':median(new),'cross_project_cold_ms':round(cold,3),
                      'cross_project_cached_ms':median(lambda:cross_project_ids(net,proj)),
                      '1000_attempt_updates_with_rollups_ms':write_with_rollups,
                      '1000_attempt_updates_without_rollups_ms':write_without_rollups}))
