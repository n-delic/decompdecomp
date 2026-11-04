"""Synthetic inspector history benchmark; never opens an existing project database."""
import json
import statistics
import tempfile
import time
from pathlib import Path

from decompnet.app.function_history import history
from decompnet.store import Store


def main():
    with tempfile.TemporaryDirectory(prefix='decomp-history-bench-') as scratch:
        store = Store(Path(scratch)/'history.db')
        with store.tx() as db:
            pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('bench',?,'{}')",(scratch,)).lastrowid
            fid=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'synthetic')",(pid,)).lastrowid
            db.executemany('INSERT INTO attempts(function_id,score,created_at) VALUES(?,?,?)',
                           [(fid,None if i%97==0 else 10000-i%9999,i) for i in range(20000)])
        def old():
            rows=store.all('SELECT t.score,t.created_at,a.name AS agent FROM attempts t LEFT JOIN agents a '
                           'ON a.id=t.agent_id WHERE t.function_id=? ORDER BY t.id',fid)
            return {'scores':[{'score':r['score'],'t':r['created_at'],'agent':r['agent']} for r in rows]}
        def new():
            return history(store,{'id':fid,'project_id':pid})
        for label,build in [('full history',old),('bounded history',new)]:
            values=[]
            for _ in range(12):
                started=time.perf_counter()
                result=build()
                wire=json.dumps(result,separators=(',',':')).encode()
                values.append((time.perf_counter()-started)*1000)
            print(f'{label}: median {statistics.median(values):.2f}ms; {len(result["scores"])} points; {len(wire):,} JSON bytes')
        store.conn.close()


if __name__=='__main__':
    main()
