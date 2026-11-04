"""A default-profile compatibility test is diagnostic and checks the exact requested population."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from decompnet.app import compiler_default_test as trial
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    cfg = {'kind':'rom','platform':'n64','compiler':{'toolchain':'ido-5.3','flags':['-O2']},
           'compiler_ranges':[{'start':0x80001020,'end':0x80001040,'toolchain':'ido-5.3','flags':['-O1']}]}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(1,?,?,?,?)',('game','Game',str(tmp_path),json.dumps(cfg)))
        for i,status in enumerate(['matched','matched','matched','open']):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,matched_source,best_source,best_score) VALUES(1,?,?,?,?,?,?,?,?)',
                       (f'f{i}',0x80001000+16*i,16,4,status,f'int f{i}(void){{return 1;}}' if status=='matched' else None,'draft',0))
    monkeypatch.setattr(trial.toolchains,'installed',lambda tc:object())
    monkeypatch.setattr(net,'_project',lambda slug:net.store.one('SELECT * FROM projects WHERE slug=?',slug))
    return net


PROFILE={'toolchain':'ido-5.3','flags':['-O1']}


def test_only_default_matches_are_checked_and_nothing_is_changed(net, monkeypatch):
    before_config=net._project('game')['config']
    before_rows=[dict(r) for r in net.store.all('SELECT * FROM functions ORDER BY id')]
    seen=[]; progress=[]
    def judge(proj, fn, tc, flags, source):
        seen.append(fn['name'])
        assert flags==['-O1'] and source==fn['matched_source']
        return fn['name']=='f0',NS(score=12),''
    monkeypatch.setattr(net,'_judge_with',judge)
    def log(line):pass
    log.progress=lambda p:progress.append(copy.deepcopy(p))
    out=trial.run(net,'game',PROFILE,log)
    assert set(seen)=={'f0','f1'}
    assert out['total']==out['checked']==2 and out['passed']==out['failed']==1
    assert not out['compatible'] and not out['stale']
    assert out['failures'][0]['function']=='f1'
    assert [p['checked'] for p in progress]==[0,1,2]
    assert all(p['passed']+p['failed']==p['checked'] for p in progress)
    assert net._project('game')['config']==before_config
    assert [dict(r) for r in net.store.all('SELECT * FROM functions ORDER BY id')]==before_rows
    assert not net.store.all('SELECT * FROM attempts') and not net.store.all('SELECT * FROM events')


def test_all_passing_and_empty_population(net, monkeypatch):
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,None,''))
    assert trial.run(net,'game',PROFILE)['compatible']
    with net.store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name IN ('f0','f1')")
    out=trial.run(net,'game',PROFILE)
    assert out['total']==0 and not out['compatible']


def test_changed_source_marks_report_stale(net, monkeypatch):
    def judge(*args):
        with net.store.tx() as db:db.execute("UPDATE functions SET matched_source='changed' WHERE name='f0'")
        return True,None,''
    monkeypatch.setattr(net,'_judge_with',judge)
    out=trial.run(net,'game',PROFILE)
    assert out['passed']==2 and out['stale'] and not out['compatible']


def test_compile_error_counts_as_failure(net, monkeypatch):
    monkeypatch.setattr(net,'_judge_with',lambda *a:(False,None,'compile error'))
    out=trial.run(net,'game',PROFILE)
    assert out['failed']==2 and not out['compatible']


@pytest.mark.parametrize('profile',[None,{}, {'toolchain':'ido-5.3','flags':'-O1'}, {'toolchain':'ido-5.3','flags':['-O2']}, {'toolchain':'missing','flags':[]}])
def test_rejects_invalid_default_or_unused_profile(net, profile):
    with pytest.raises(NetworkError):trial.run(net,'game',profile)


def test_cancellation_never_changes_config(net, monkeypatch):
    before=net._project('game')['config']
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,None,''))
    def log(line):pass
    def cancelled():raise RuntimeError('cancelled')
    log.check_cancel=cancelled
    with pytest.raises(RuntimeError,match='cancelled'):trial.run(net,'game',PROFILE,log)
    assert net._project('game')['config']==before
