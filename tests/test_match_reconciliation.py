import json
import threading
from types import SimpleNamespace

import pytest

from decompnet import match_bundle
from decompnet.app import match_reconciliation as recovery
from tests.test_prepared_evidence import prepared_net


def setup(monkeypatch, *, best=20, exact=True):
    from decompnet.app.permute import Permuter
    net,proj,fn=prepared_net()
    net.is_rom=lambda p:True;net.fork_slug=lambda s:None;net.links={}
    net.project_row=lambda slug:net.store.one('SELECT * FROM projects WHERE id=1')
    net.local_agent=lambda name:(SimpleNamespace(id=1,name='autopilot'), '')
    net.permuter=Permuter(net)
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
        db.execute('UPDATE functions SET best_score=?,matched_source=NULL', (best,))
    monkeypatch.setattr(recovery,'bank',lambda *a:[])
    monkeypatch.setattr(recovery,'candidates',lambda *a:[('int f(void){return 1;}','private types','historical')])
    monkeypatch.setattr(recovery.candidate_scope,'key',lambda *a:'scope')
    settings=json.loads(proj['config'])['compiler']
    net._compile=lambda *a:SimpleNamespace(ok=True,obj=b'fixture',effective_settings={'compiler':settings})
    net._evaluate=lambda *a:(object(),exact,[])
    log=lambda text:None
    log.cancel_event=threading.Event();log.progress=lambda p:None
    return net,log


@pytest.mark.parametrize('best',[20,0])
def test_only_fresh_exact_adopts_private_header_even_if_old_open_score_is_zero(monkeypatch,best):
    net,log=setup(monkeypatch,best=best)
    out=recovery.run(net,'game',30,1,log)
    assert out['matched']==1 and out['attempts']==1
    fn=net.store.one('SELECT * FROM functions')
    assert fn['status']=='matched' and fn['unit'].startswith('local_source_')
    assert net._context(1,fn['unit'])['header']=='private types'
    assert not net.store.one('SELECT 1 FROM claims')


def test_nonexact_cancelled_and_changed_inputs_never_promote(monkeypatch):
    net,log=setup(monkeypatch,exact=False)
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'
    net,log=setup(monkeypatch)
    def compile(*a):
        log.cancel_event.set()
        return SimpleNamespace(ok=True,obj=b'fixture')
    net._compile=compile
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'


def test_source_aliasing_preserves_strings_and_requires_target_identity(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True
    with net.store.tx() as db:db.execute("UPDATE functions SET target=X'01020304'")
    fn=net.store.one('SELECT * FROM functions')
    source='int old(void){say("old");return 1;}'
    proof=dict(abi=match_bundle.abi(proj,json.loads(proj['config'])),vram=fn['vram'],target_sha256=match_bundle.digest(fn['target']),
               relocations_sha256=match_bundle.digest(fn['relocs'] or ''),header_sha256=match_bundle.digest('private'))
    row=dict(name='old',source=source,header='private',meta='{}',vram=fn['vram'])
    with net.store.tx() as db:db.execute('DELETE FROM attempts')
    out=recovery.candidates(net,proj,fn,[(row,proof)])
    assert any('int f(void)' in s and 'say("old")' in s for s,h,o in out)
    assert not recovery.candidates(net,proj,fn,[(row,{**proof,'target_sha256':'changed'})])


def test_failed_compiler_candidates_rotate_across_runs(monkeypatch):
    net,log=setup(monkeypatch)
    monkeypatch.setattr(recovery,'candidates',lambda *a:[('first','','old'),('second','','old')])
    seen=[]
    net._compile=lambda p,h,s,f:seen.append(s) or SimpleNamespace(ok=False)
    recovery.run(net,'game',30,1,log);recovery.run(net,'game',30,1,log)
    assert seen==['first','second']


def test_changed_inputs_and_busy_claim_are_not_adopted(monkeypatch):
    net,log=setup(monkeypatch)
    def changed(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source='concurrent edit'")
        return SimpleNamespace(ok=True,obj=b'fixture')
    net._compile=changed
    assert recovery.run(net,'game',30,1,log)['matched']==0
    assert net.store.one('SELECT status FROM functions')['status']=='open'
    net,log=setup(monkeypatch)
    monkeypatch.setattr(net.permuter,'_hold',lambda *a,**k:False)
    assert recovery.run(net,'game',30,1,log)['attempts']==0


def test_restore_flag_cannot_bypass_nonexact_or_missing_frozen_context():
    from decompnet.network import Agent, NetworkError
    net,_,fn=prepared_net()
    with pytest.raises(NetworkError):
        net._record(fn,Agent(1,'autopilot',None),1,'bad',0,matched=False,restoring_exact=True)
