"""Proposal arrival, speculative compilation and diagnostic reuse retain bounded exact judging."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet.app import deep_proposals, draft_repairs
from decompnet.app.deep_repair_feedback import Feedback
from tests.test_deep_proposals import request


@pytest.mark.parametrize('source',[
    None,
    'int f(int a,int b){int x=a; int y=b; return x+y;}',
    'int f(int *p,int n){int i;int sum=0;for(i=0;i<n;i++)sum+=p[i];return sum;}',
    '#define PLUS(a,b) ((a)+(b))\nint f(int a,int b){return PLUS(a,b);}',
    'int f(int a){\n#define VALUE(x) ((x)+1)\nreturn VALUE(a);\n}',
])
def test_parallel_generation_preserves_the_serial_candidate_set(source):
    args,kwargs=request()
    if source is not None:args=(source,'f',*args[2:])
    labels={};expected=draft_repairs.variants(*args,labels=labels,**kwargs)
    pool=deep_proposals.Pool(3)
    try:
        batches=list(pool.stream(args,kwargs,lambda:None))
        choices={choice for batch,_,_ in batches for choice in batch}
        assert choices==set(expected)
        assert all(choice in tagged for batch,tagged,_ in batches for choice in batch)
        assert pool.submitted==3
        list(pool.stream(args,kwargs,lambda:None))
        assert pool.submitted==3
    finally:pool.close()


def test_first_batch_arrives_before_slow_families_finish_and_requests_are_shared(monkeypatch):
    release=threading.Event();slow=threading.Event();calls=[]
    class Worker:
        def __init__(self):self.closed=threading.Event()
        def run(self,payload,check):
            _,kwargs=json.loads(payload);index,count=kwargs['_partition'];calls.append(index)
            if index:
                slow.set();assert release.wait(5)
            return {'choices':[str(index)],'labels':{str(index):'family'}}
        def close(self):self.closed.set()
    monkeypatch.setattr(deep_proposals,'Worker',Worker)
    pool=deep_proposals.Pool(2);args,kwargs=request()
    try:
        stream=pool.stream(args,kwargs,lambda:None)
        assert next(stream)[0]==['0'] and slow.wait(2)
        with ThreadPoolExecutor(1) as threads:
            other=threads.submit(lambda:list(pool.stream(args,kwargs,lambda:None)))
            release.set()
            assert {c for batch,_,_ in other.result(3) for c in batch}=={'0','1'}
        assert list(stream)[0][0]==['1'] and sorted(calls)==[0,1]
    finally:release.set();pool.close()


def test_feedback_reuses_pending_nodes_and_invalidates_changed_parent_proofs():
    feedback=Feedback('base',40,[],{'hash':'old'})
    labels={'a':'one','b':'two'};queue=['a','b']
    feedback.order(queue,labels,'base',{})
    first={node['source']:node for node in feedback.state['pending']}
    feedback.order(queue,labels,'base',{})
    assert all(first[node['source']] is node for node in feedback.state['pending'])
    feedback.proofs['base']={**feedback.proofs['base'],'output':{'hash':'new'}}
    feedback.order(queue,labels,'base',{})
    assert all(first[node['source']] is not node for node in feedback.state['pending'])
    queue.remove('a');feedback.order(queue,labels,'base',{})
    assert set(feedback.nodes)=={'b'}


def test_local_search_judges_first_streamed_batch_before_requesting_the_rest(monkeypatch):
    from decompnet.app import deep_compilation, deep_permuter
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    net=setup(monkeypatch);events=[]
    def stream(*args):
        events.append('first batch')
        yield ['local'],{'local':'test_family'},0
        events.append('requested rest')
        pytest.fail('An exact first result must not wait for the rest of generation')
    pool=deep_compilation.Pool(1,maximum=True)
    monkeypatch.setattr(pool,'proposal_pool',lambda:SimpleNamespace(stream=stream))
    def score(self,agent,proj,fn,source,best,fork):
        events.append('judged '+source)
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0,best_source=? WHERE id=?",(source,fn['id']))
        return 0
    monkeypatch.setattr(Permuter,'_score',score)
    log=Log();log.compile_pool=pool
    try:
        result=deep_permuter.run(net,'game','f',1,8,log)
        assert result['matched'] and events==['first batch','judged local']
    finally:pool.close()


def test_partition_keeps_nested_generation_stages_together():
    import hashlib

    from decompnet.app.repair_generation import PARTITION, call
    def inner():return ['nested candidate']
    def outer():return call(inner)
    inner_hash=int.from_bytes(hashlib.sha256((inner.__module__+'.'+inner.__qualname__).encode()).digest()[:4],'big')
    outer_hash=int.from_bytes(hashlib.sha256((outer.__module__+'.'+outer.__qualname__).encode()).digest()[:4],'big')
    count=next(size for size in range(2,32) if inner_hash%size!=outer_hash%size)
    # A nested stage belongs to the admitted outer family, even when its own
    # standalone hash would select another worker.
    results=[]
    for index in range(count):
        token=PARTITION.set((index,count))
        try:results.extend(call(outer))
        finally:PARTITION.reset(token)
    assert results==['nested candidate']
