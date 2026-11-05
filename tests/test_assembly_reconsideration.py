"""Capability review retires invalid tooling votes without reopening genuine assembly."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import assembly_reconsideration as review
from decompnet.app.engine import RomNetwork


@pytest.fixture
def game(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                       ('game','Game','','mips',1,str(tmp_path),json.dumps({'kind':'rom','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[]}}))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,?,?,?,?)",(pid,1,'',0,''))
        fid=db.execute("INSERT INTO functions(project_id,name,status,unit,best_source,asm,meta) VALUES(?,?,?,?,?,?,?)",
                       (pid,'func_80001000','asm','','source','target',json.dumps({'not_c':{'agent':{'why':'__builtin_return_address unsupported'}},'asm_code':'old verdict'}))).lastrowid
    monkeypatch.setattr(review.toolchains,'get',lambda _:SimpleNamespace())
    monkeypatch.setattr(review.toolchains,'source_prelude',lambda *_:'__builtin_mips_live_return_address')
    monkeypatch.setattr(review.live_return_address_repairs,'variants',lambda *_:[('live_return_address','raw'),('live_return_address_initializers','bundle')])
    return net,net.project_row('game'),fid


def test_reopens_obsolete_votes_and_runs_ordinary_judge(game,monkeypatch):
    net,proj,fid=game;seen=[]
    def judge(agent,project,fn,source):
        assert fn['status']=='open'
        seen.append(source)
        if source=='bundle':
            with net.store.tx() as db:db.execute("UPDATE functions SET status='matched',best_score=0 WHERE id=?",(fid,))
            return {'matched':True}
        return {'matched':False,'score':270}
    monkeypatch.setattr(net,'try_quietly',judge)
    assert review.run(net,proj)==1 and seen==['raw','bundle']
    fn=net.store.one('SELECT * FROM functions WHERE id=?',fid)
    assert fn['status']=='matched' and fn['best_score']==0
    meta=json.loads(fn['meta']);assert 'not_c' not in meta and meta['capability_review']['not_c']
    assert review.run(net,proj)==0


def test_nonexact_remains_open_for_further_work(game,monkeypatch):
    net,proj,fid=game
    monkeypatch.setattr(net,'try_quietly',lambda *_:{'matched':False,'score':10})
    assert review.run(net,proj)==1
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='open'


@pytest.mark.parametrize('change',[{'not_c':{'you':{'why':'__builtin_return_address unsupported'}}},
    {'not_c':{'agent':{'why':'handwritten condition flag protocol'}}},
    {'set_aside':'library'},{'asm_twin':True},{'verified_assembly':{'verified':True}},{'boundary_review':True}])
def test_valid_or_explicit_assembly_untouched(game,change):
    net,proj,fid=game
    fn=net.store.one('SELECT * FROM functions WHERE id=?',fid);meta=json.loads(fn['meta']);meta.update(change)
    with net.store.tx() as db:db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),fid))
    assert review.run(net,proj)==0
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='asm'


def test_claimed_and_unsupported_untouched(game,monkeypatch):
    net,proj,fid=game;agent,_=net.local_agent('worker')
    with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,agent.id,0,9999999999))
    assert review.run(net,proj)==0
    with net.store.tx() as db:db.execute('DELETE FROM claims WHERE function_id=?',(fid,))
    monkeypatch.setattr(review.toolchains,'source_prelude',lambda *_:'')
    assert review.run(net,proj)==0
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='asm'


def test_no_target_backed_proposal_stays_assembly(game,monkeypatch):
    net,proj,fid=game
    monkeypatch.setattr(review.live_return_address_repairs,'variants',lambda *_:[])
    assert review.run(net,proj)==0
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='asm'
