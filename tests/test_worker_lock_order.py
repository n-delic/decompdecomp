"""Worker waits must be bounded, and initialization must follow database lock order."""
import threading
from contextlib import contextmanager

import pytest

from decompnet import read_cache
from decompnet.app import deep_sweep, experiment_evidence, knowledge, proposal_progress, session_storage, view_snapshots
from decompnet.read_cache import ReadCache

from .test_claims import _game


def test_recursive_read_cache_builder_fails_without_poisoning_entry():
    cache=ReadCache()
    with pytest.raises(RuntimeError,match='recursively'):
        cache.get('key',1,lambda:cache.get('key',1,lambda:42))
    assert cache.get('key',1,lambda:42)==42


def test_stalled_read_cache_waiter_times_out_without_duplicate_build(monkeypatch):
    monkeypatch.setattr(read_cache,'READ_WAIT_SECONDS',.05)
    cache=ReadCache();entered=threading.Event();release=threading.Event();result=[]
    def build():
        entered.set();assert release.wait(3)
        return 42
    owner=threading.Thread(target=lambda:result.append(cache.get('key',1,build)),daemon=True)
    owner.start();assert entered.wait(1)
    try:
        with pytest.raises(TimeoutError,match='metadata'):
            cache.get('key',1,lambda:pytest.fail('duplicate build'))
        assert cache.get('unrelated',1,lambda:7)==7
    finally:release.set();owner.join(2)
    assert result==[42]
    assert cache.get('key',1,lambda:pytest.fail('lost result'))==42
    assert cache.entries['key']['users']==0


def test_cross_key_cache_cycle_breaks_and_entries_can_recover(monkeypatch):
    monkeypatch.setattr(read_cache,'READ_WAIT_SECONDS',.05)
    cache=ReadCache();barrier=threading.Barrier(2);errors=[]
    def run(key,other):
        def build():
            barrier.wait(1)
            return cache.get(other,1,lambda:42)
        try:cache.get(key,1,build)
        except TimeoutError as error:errors.append(error)
    threads=[threading.Thread(target=run,args=pair,daemon=True) for pair in [('a','b'),('b','a')]]
    for thread in threads:thread.start()
    for thread in threads:thread.join(2)
    assert all(not thread.is_alive() for thread in threads)
    assert errors
    assert cache.get('a',2,lambda:10)==10
    assert cache.get('b',2,lambda:20)==20


def test_map_builder_wait_is_bounded(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('f',40,'open')])
    monkeypatch.setattr(view_snapshots,'SNAPSHOT_WAIT_SECONDS',.05)
    lock=threading.Lock();view_snapshots.cache(net).map_locks[pid]=lock
    lock.acquire()
    try:
        with pytest.raises(TimeoutError,match='function map'):
            view_snapshots.incremental_map(net,'game')
    finally:lock.release()
    assert view_snapshots.incremental_map(net,'game')[0]['name']=='f'


def initializer(net, kind):
    if kind == 'sessions':
        return lambda:session_storage.prepare(net,'game'), lambda:net.__dict__.get('_session_storage_ready',set())
    if kind in ('knowledge','deep-sweep'):
        cls=knowledge.Knowledge if kind=='knowledge' else deep_sweep.Sweeps
        instance=object.__new__(cls);instance.net=net;instance.ready=set();instance.lock=threading.RLock()
        return lambda:instance._prepare(net.store), lambda:instance.ready
    module,key=(experiment_evidence,'_repair_input_effects_stores') if kind=='experiments' else (proposal_progress,'_proposal_progress_stores')
    return lambda:module.prepare(net,'game'),lambda:net.__dict__.get(key,set())


@pytest.mark.parametrize('kind',['sessions','knowledge','deep-sweep','experiments','proposals'])

def test_session_initialization_does_not_invert_writer_order(tmp_path,monkeypatch,kind):
    net,_=_game(tmp_path,[('f',40,'open')]);store=net.store
    prepare,ready=initializer(net,kind)
    original=store.tx;writer_held=threading.Event();outsider_waiting=threading.Event();done=threading.Event();errors=[]
    @contextmanager
    def tx(*args,**kwargs):
        if threading.current_thread().name=='session-outsider':outsider_waiting.set()
        with original(*args,**kwargs) as db:yield db
    monkeypatch.setattr(store,'tx',tx)
    def inside():
        try:
            with store.tx():
                writer_held.set();assert outsider_waiting.wait(2)
                prepare()
        except BaseException as error:errors.append(error)
        finally:done.set()
    holder=threading.Thread(target=inside,daemon=True);holder.start();assert writer_held.wait(1)
    outsider=threading.Thread(target=prepare,name='session-outsider',daemon=True)
    outsider.start()
    assert done.wait(3),'session initializer and database writer deadlocked'
    holder.join(1);outsider.join(2)
    assert not outsider.is_alive()
    assert not errors
    assert store in ready()


@pytest.mark.parametrize('kind',['sessions','knowledge','deep-sweep','experiments','proposals'])
def test_session_schema_readiness_waits_for_outer_commit(tmp_path,kind):
    net,_=_game(tmp_path,[('f',40,'open')]);store=net.store
    prepare,ready=initializer(net,kind)
    with pytest.raises(ValueError,match='rollback'):
        with store.tx():
            prepare()
            assert store not in ready()
            raise ValueError('rollback')
    assert store not in ready()
    prepare()
    assert store in ready()


def test_lazy_knowledge_service_takes_writer_gate_before_connection_mutex(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',40,'open')]);store=net.store
    net.__dict__.pop('knowledge',None)
    original=store.writer_gate.hold;entered=threading.Event();done=threading.Event();errors=[]
    @contextmanager
    def hold(**kwargs):
        if threading.current_thread().name=='knowledge-init':entered.set()
        with original(**kwargs) as admitted:yield admitted
    monkeypatch.setattr(store.writer_gate,'hold',hold)
    def initialize():
        try:knowledge.service(net)
        except BaseException as error:errors.append(error)
        finally:done.set()
    with original():
        thread=threading.Thread(target=initialize,name='knowledge-init',daemon=True);thread.start()
        assert entered.wait(2)
        acquired=store.lock.acquire(timeout=.2)
        if acquired:store.lock.release()
    assert done.wait(3)
    thread.join(1)
    assert acquired,'initializer held the connection mutex while waiting for writer admission'
    assert not errors


@pytest.mark.parametrize('grouped',[False,True])
def test_compilation_sharing_detects_recursive_builder(grouped):
    from decompnet import compile_sharing
    from decompnet.compiler import CompileResult
    group=compile_sharing.Group()
    def run(build):return group.run('key',build,None) if grouped else compile_sharing.run('key',build)
    with pytest.raises(RuntimeError,match='own builder'):run(lambda:run(lambda:None))
    assert run(lambda:CompileResult(True,b'object','')).ok


@pytest.mark.parametrize('grouped',[False,True])
def test_compilation_sharing_wait_is_bounded(grouped,monkeypatch):
    from decompnet import compile_sharing
    from decompnet.compiler import CompileResult
    monkeypatch.setattr(compile_sharing,'SHARED_WAIT_SECONDS',.05)
    group=compile_sharing.Group();entered=threading.Event();release=threading.Event();results=[]
    def run(build):return group.run('shared-bounded',build,None) if grouped else compile_sharing.run('shared-bounded',build)
    def build():
        entered.set();assert release.wait(3)
        return CompileResult(True,b'object','')
    thread=threading.Thread(target=lambda:results.append(run(build)),daemon=True)
    thread.start();assert entered.wait(1)
    try:
        with pytest.raises(TimeoutError,match='shared compilation'):run(lambda:pytest.fail('duplicate compile'))
    finally:release.set();thread.join(2)
    assert results[0].ok
