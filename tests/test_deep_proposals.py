"""Local repair workers share exact generation inputs, never compiler verdicts or database handles."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import deep_proposals, draft_repairs


def request():
    return ('int f(int x) { return x + 1; }','f','mips','',[]),dict(limit=None,header='',tables=[],callees='',entry_scope={})


def test_real_worker_matches_in_process_proposals_and_reuses_process():
    args,kwargs=request()
    labels={};expected=draft_repairs.variants(*args,labels=labels,**kwargs)
    pool=deep_proposals.Pool(2)
    try:
        choices,got,_=pool.generate(args,kwargs,lambda:None)
        assert choices==expected and got==labels
        choices.clear();got.clear()
        again=pool.generate(args,kwargs,lambda:None)
        assert again[0]==expected and again[1]==labels
        assert pool.submitted==1 and len(pool.workers)==1
        worker=pool.workers[0]
        changed=(*args[:1],*args[1:])
        pool.generate(changed,dict(kwargs,header='typedef int new_type;'),lambda:None)
        assert pool.submitted==2 and len(pool.workers)==1
    finally:pool.close()
    assert worker.process.poll() is not None and not worker.reader.is_alive()


def test_parallel_paths_share_one_generation_without_shared_mutation(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    calls=[]
    class Worker:
        closed=threading.Event()
        def run(self,payload,check):
            calls.append(payload);entered.set();assert release.wait(3)
            return {'choices':['candidate'],'labels':{'candidate':'family'}}
        def close(self):pass
    monkeypatch.setattr(deep_proposals,'Worker',Worker)
    pool=deep_proposals.Pool(4)
    args,kwargs=request()
    try:
        with ThreadPoolExecutor(4) as threads:
            futures=[threads.submit(pool.generate,args,kwargs,lambda:None) for _ in range(4)]
            assert entered.wait(2);release.set()
            results=[f.result(3) for f in futures]
        assert len(calls)==1 and pool.submitted==1
        results[0][0].append('other')
        assert results[1][0]==['candidate']
    finally:pool.close()


def test_worker_is_killed_promptly_on_cancellation(monkeypatch):
    import json
    import sys
    original=deep_proposals.subprocess.Popen
    def process(*args,**kwargs):
        return original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
    monkeypatch.setattr(deep_proposals.subprocess,'Popen',process)
    worker=deep_proposals.Worker()
    count=[0]
    def check():
        count[0]+=1
        if count[0]>2:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        worker.run(json.dumps(request()).encode(),check)
    assert worker.process.poll() is not None and not worker.reader.is_alive()


def test_generator_cache_key_covers_header_flags_assembly_and_source(monkeypatch):
    calls=[]
    class Worker:
        closed=threading.Event()
        def run(self,payload,check):calls.append(payload);return {'choices':['x'],'labels':{'x':'f'}}
        def close(self):pass
    monkeypatch.setattr(deep_proposals,'Worker',Worker)
    pool=deep_proposals.Pool(1)
    args,kwargs=request()
    try:
        pool.generate(args,kwargs,lambda:None)
        for index,value in [(0,args[0]+'\n'),(3,'different asm'),(4,['-O2'])]:
            changed=list(args);changed[index]=value
            pool.generate(changed,kwargs,lambda:None)
        pool.generate(args,dict(kwargs,header='int changed;'),lambda:None)
        assert len(calls)==5
    finally:pool.close()


def test_pool_shutdown_cancels_stuck_generation_and_releases_waiter(monkeypatch):
    import sys
    import time
    original=deep_proposals.subprocess.Popen
    def process(*args,**kwargs):return original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
    monkeypatch.setattr(deep_proposals.subprocess,'Popen',process)
    pool=deep_proposals.Pool(1)
    args,kwargs=request()
    with ThreadPoolExecutor(1) as callers:
        waiting=callers.submit(pool.generate,args,kwargs,lambda:None)
        for _ in range(100):
            if pool.workers:break
            time.sleep(.01)
        assert pool.workers
        started=time.monotonic();pool.close()
        assert time.monotonic()-started<3
        with pytest.raises(RuntimeError):waiting.result(timeout=1)
    assert all(worker.process.poll() is not None for worker in pool.workers)


def test_completed_proposal_cache_is_size_bounded(monkeypatch):
    monkeypatch.setattr(deep_proposals,'MAX_CACHE',1)
    class Worker:
        closed=threading.Event()
        def run(self,payload,check):return {'choices':['large proposal'],'labels':{'large proposal':'family'}}
        def close(self):pass
    monkeypatch.setattr(deep_proposals,'Worker',Worker)
    pool=deep_proposals.Pool(1)
    try:
        for _ in range(3):
            args,kwargs=request();assert pool.generate(args,kwargs,lambda:None)[0]==['large proposal']
        pool.executor.shutdown(wait=True)
        assert not pool.cache and not pool.sizes
    finally:pool.close()
