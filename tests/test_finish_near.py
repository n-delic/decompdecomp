import threading
from types import SimpleNamespace

import pytest

from decompnet.app import finish_near, first_difference, prototype_reuse


def test_first_operand_overlap_ranks_edits_without_dropping_exploration():
    source='int f(void){int x;x=4;return x;}'
    options=[('other',source.replace('4','5')),('constant',source.replace('4','32'))]
    rows=[('i','addiu $a0, $zero, 32','addiu $a0, $zero, 4')]
    assert first_difference.order(source,options,rows)[0][0]=='constant'
    assert len(first_difference.order(source,options*3,rows))==6
    assert first_difference.order(source,options,[])==options


def test_verified_prototype_is_candidate_only_and_conflicts_are_rejected():
    source='short read(int); int f(void){return read(2);}'
    donors=['int read(int); int known(void){return read(3);}']
    out=prototype_reuse.apply(source,'f',donors)
    assert out and 'int read(int);' in out[0][1]
    assert source.startswith('short read')
    assert not prototype_reuse.apply(source,'f',donors+['char read(int);'])
    assert not prototype_reuse.apply('short read(...); int f(void){return read(2);}','f',donors)
    assert not prototype_reuse.apply('#define read hidden\n'+source,'f',donors)


def test_pass_validates_budgets_and_does_not_start_a_provider():
    net=SimpleNamespace(project_row=lambda s:dict(arch='mips'),is_rom=lambda p:True,fork_slug=lambda s:False,links={})
    assert finish_near.validate(net,'game',120,128)
    for seconds,attempts in [(True,10),(0,10),(10,1025)]:
        with pytest.raises(ValueError):finish_near.validate(net,'game',seconds,attempts)


def test_finish_pass_uses_cancel_event_and_existing_iterative_budget(monkeypatch):
    event=threading.Event();captured={};progress=[]
    def log(text):pass
    log.progress=progress.append;log.cancel_event=event
    net=SimpleNamespace(twins=object())
    monkeypatch.setattr(finish_near,'validate',lambda *a:None)
    def run(twins,slug,job):
        assert job.stop is event and job.finish_near
        assert job.reuse_limit==12
        job.reuse_matched=2;job.reuse_attempts=4
        job.near_progress(1,3,'function')
        captured['job']=job
        return 2
    monkeypatch.setattr(finish_near.iterative_repairs,'run',run)
    result=finish_near.run(net,'game',30,12,log)
    assert result['matched']==2 and result['attempts']==4 and result['resumable']
    assert progress[0]['total']==3


def test_stale_or_regressed_donors_never_supply_prototypes(monkeypatch):
    net=SimpleNamespace(is_rom=lambda p:True,cfg=lambda p:{'compiler':{'toolchain':'test'}},
        store=SimpleNamespace(all=lambda *args:[{'vram':2}]))
    net.project_store = lambda _project: net.store
    monkeypatch.setattr(prototype_reuse,'compiler_for',lambda *args:{'toolchain':'test'})
    source='short read(int); int f(void){return read(2);}'
    for details in [None,dict(changed_inputs=['header changed'],source='int read(int);'),dict(changed_inputs=[],regression={'score':1},source='int read(int);')]:
        monkeypatch.setattr(prototype_reuse.match_bundle,'status',lambda *args,details=details:details)
        assert not prototype_reuse.variants(net,{'id':1},{'id':3,'name':'f','vram':3},source)
    monkeypatch.setattr(prototype_reuse.match_bundle,'status',lambda *args:dict(changed_inputs=[],regression=None,source='int read(int);'))
    assert prototype_reuse.variants(net,{'id':1},{'id':3,'name':'f','vram':3},source)


def test_fair_queue_prefers_unvisited_and_changed_inputs(tmp_path,monkeypatch):
    from decompnet.store import Store
    store=Store(tmp_path/'queue.db')
    net=SimpleNamespace(store=store,project_store=lambda _:store)
    monkeypatch.setattr(finish_near,'scope',lambda net,proj,fn:fn['best_source'])
    rows=[dict(id=11,best_source='one',best_score=1,n_insns=100),dict(id=12,best_source='two',best_score=2,n_insns=100)]
    assert finish_near.ordered(net,{'id':1},rows)[0]['id']==11
    finish_near.visited(net,{'id':1},rows[0])
    assert finish_near.ordered(net,{'id':1},rows)[0]['id']==12
    rows[0]['best_source']='changed'
    assert finish_near.ordered(net,{'id':1},rows)[0]['id']==11


def test_panel_progress_and_stop_handler():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('Needs node for the near-match panel')
    root=Path(__file__).parents[1]
    subprocess.run([node,str(root/'tests/gui_finish_near.js')],cwd=root,check=True,capture_output=True,text=True,timeout=30)


def test_duplicate_project_phases_do_not_overwrite_frontiers(monkeypatch):
    from decompnet.app import iterative_repairs
    twins=SimpleNamespace(net=SimpleNamespace())
    calls=[]
    def work(twins,slug,job):
        calls.append(slug)
        assert iterative_repairs.run(twins,slug,job)==0
        return 1
    monkeypatch.setattr(iterative_repairs,'_run',work)
    assert iterative_repairs.run(twins,'game',object())==1
    assert calls==['game']
    assert not twins.net._iterative_phase_locks['game'].locked()
