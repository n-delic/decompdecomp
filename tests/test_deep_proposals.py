"""Local repair workers share exact generation inputs, never compiler verdicts or database handles."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.app import deep_proposals, draft_repairs


def request():
    return ('int f(int x) { int y; y=x+1; return y; }','f','mips','',[]),dict(limit=None,header='',tables=[],callees='',entry_scope={})


def test_real_worker_matches_in_process_proposals_and_reuses_process():
    args,kwargs=request()
    labels={};expected=draft_repairs.variants(*args,labels=labels,**kwargs)
    assert expected and labels
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


@pytest.mark.parametrize('request_size', [0, 2 << 20])
def test_worker_is_killed_promptly_on_cancellation(monkeypatch, request_size):
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
        worker.run(json.dumps(request()).encode() + b" " * request_size,check)
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


def test_cached_worker_result_does_not_count_proposal_work_twice(monkeypatch):
    from decompnet import compile_timing
    class Worker:
        closed=threading.Event()
        def run(self,payload,check):
            return {'choices':['c'],'labels':{'c':'family'},'timings':{'proposal_generation':2.5}}
        def close(self):pass
    monkeypatch.setattr(deep_proposals,'Worker',Worker)
    pool=deep_proposals.Pool(1)
    counts=compile_timing.Counts()
    try:
        args,kwargs=request()
        with compile_timing.collect(counts):
            pool.generate(args,kwargs,lambda:None)
            pool.generate(args,kwargs,lambda:None)
        assert counts.values['proposal_generation']==2.5
        assert pool.submitted==1
        # Evict the cached result; newly executed work with the same request counts again.
        pool.cache.clear();pool.sizes.clear()
        with compile_timing.collect(counts):pool.generate(args,kwargs,lambda:None)
        assert counts.values['proposal_generation']==5
    finally:pool.close()


def test_balanced_workers_preserve_candidate_order_and_labels():
    args, kwargs = request()
    labels = {};expected = draft_repairs.variants(*args, labels=labels, **kwargs)
    pool = deep_proposals.Pool(2, background=True)
    try:
        choices, got, _ = pool.generate(args, kwargs, lambda:None)
        assert choices == expected and got == labels
        assert '_background' not in kwargs
    finally:pool.close()


def test_balanced_compile_pool_uses_bounded_background_proposal_workers():
    from decompnet.app.deep_compilation import Pool
    for maximum in (False, True):
        pool = Pool(3, maximum=maximum)
        try:
            proposals = pool.proposal_pool()
            assert proposals is pool.proposal_pool()
            assert proposals.count == 3
            assert proposals.background is not maximum
        finally:pool.close()


@pytest.mark.parametrize('background', [False, True])
def test_worker_applies_background_priority_once_and_keeps_metadata_out_of_repairs(monkeypatch, background):
    import io
    import json
    from types import SimpleNamespace

    from decompnet.app import deep_proposal_worker, workers

    args, kwargs = request();kwargs['_background'] = background
    data = (json.dumps([args, kwargs])+'\n').encode()*2
    output = io.BytesIO();priorities = [];received = []
    monkeypatch.setattr(deep_proposal_worker, 'sys', SimpleNamespace(
        stdin=SimpleNamespace(buffer=io.BytesIO(data)), stdout=SimpleNamespace(buffer=output)))
    monkeypatch.setattr(workers, 'started_priority', lambda:priorities.append(True))
    def variants(*args, **kwargs):
        received.append(kwargs)
        return []
    monkeypatch.setattr(draft_repairs, 'variants', variants)
    deep_proposal_worker.main()
    assert priorities == ([True] if background else [])
    assert len(received) == 2 and all('_background' not in options for options in received)
    assert all('error' not in json.loads(line) for line in output.getvalue().splitlines())


def _kill_when_asked(monkeypatch, deaths):
    """Workers that start a real subprocess which exits (as if killed) after reading the first request, `deaths` times."""
    import sys
    original = deep_proposals.subprocess.Popen
    started = []

    def process(command, **kwargs):
        if len(started) < deaths:
            started.append(1)
            return original([sys.executable, '-c', 'import os,signal,sys; sys.stdin.readline(); '
                             'sys.stderr.write("boom: out of memory\\n"); sys.stderr.flush(); os.kill(os.getpid(), signal.SIGKILL)'], **kwargs)
        return original(command, **kwargs)
    monkeypatch.setattr(deep_proposals.subprocess, 'Popen', process)
    return started


@pytest.mark.skipif(deep_proposals.os.name == 'nt', reason='signals')
def test_a_worker_that_dies_mid_request_says_how_and_the_pool_asks_a_fresh_one(monkeypatch, caplog):
    """A pass failed with 'Local proposal generator stopped' when every worker was replaced at once (tennis, 14:37)."""
    args, kwargs = request()
    expected = draft_repairs.variants(*args, **kwargs)
    started = _kill_when_asked(monkeypatch, deaths=2)
    pool = deep_proposals.Pool(1)
    try:
        with caplog.at_level('INFO', logger='decompdecomp'):
            choices, _, _ = pool.generate(args, kwargs, lambda: None)
    finally:
        pool.close()
    assert choices == expected and len(started) == 2
    text = caplog.text
    assert 'killed by signal 9' in text and 'boom: out of memory' in text and 'Asking a fresh proposal worker again' in text


@pytest.mark.skipif(deep_proposals.os.name == 'nt', reason='signals')
def test_a_request_that_keeps_killing_its_worker_fails_after_the_retries_with_the_cause(monkeypatch):
    args, kwargs = request()
    started = _kill_when_asked(monkeypatch, deaths=10)
    pool = deep_proposals.Pool(1)
    try:
        with pytest.raises(deep_proposals.WorkerStopped, match=r'killed by signal 9.*Retry the repair'):
            pool.generate(args, kwargs, lambda: None)
    finally:
        pool.close()
    assert len(started) == deep_proposals.RETRIES + 1


@pytest.mark.skipif(deep_proposals.os.name == 'nt', reason='signals')
def test_the_draft_worker_batch_asks_a_fresh_worker_when_the_idle_one_died(monkeypatch):
    import json
    args, kwargs = request()
    started = _kill_when_asked(monkeypatch, deaths=1)
    monkeypatch.setattr(draft_repairs, '_IDLE_WORKERS', [])
    payload = json.dumps([args, kwargs], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    result = draft_repairs._worker_batch(payload, lambda: None)
    assert result['candidates'] and len(started) == 1
    for worker in draft_repairs._IDLE_WORKERS:
        worker.close()
