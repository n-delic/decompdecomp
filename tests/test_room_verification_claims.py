"""Host verification reserves only its submission, even for shared saved-progress identities."""
import time
from types import SimpleNamespace

import pytest

from decompnet.app import room_matches
from decompnet.app.engine import RomNetwork
from tests.test_loading import tiny_game


@pytest.fixture
def setup(tmp_path, monkeypatch):
    net=RomNetwork(tmp_path/'app.db', tmp_path)
    tiny_game(net)
    agent,_=net.local_agent('room-saved-progress')
    fn=net._function('func_0','game')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],agent.id,time.time(),time.time()+3600))
    original=dict(net.store.one('SELECT * FROM claims'))
    monkeypatch.setattr(room_matches.toolchains,'installed',lambda tc:True)
    return net,agent,original


def test_failed_verification_does_not_block_the_next_submission(setup,monkeypatch):
    net,agent,original=setup
    compiled=[]
    def compile(proj,header,source,fn):
        compiled.append(fn['name'])
        return SimpleNamespace(ok=False,log='synthetic compile error')
    monkeypatch.setattr(net,'_attempt_compilation',compile)
    monkeypatch.setattr(net,'_record',lambda *args,**kwargs:None)
    for name in ['func_1','func_2']:
        result=room_matches.verify(net,'game',agent,name,'int candidate;')
        assert result['pending'] and 'another claim' not in result['reason']
        assert [dict(row) for row in net.store.all('SELECT * FROM claims')]==[original]
    assert compiled==['func_1','func_2']


def test_verification_never_takes_another_workers_claim(setup,monkeypatch):
    net,agent,original=setup
    other,_=net.local_agent('active-worker')
    fn=net._function('func_1','game')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],other.id,time.time(),time.time()+3600))
    before=[dict(row) for row in net.store.all('SELECT * FROM claims ORDER BY function_id')]
    monkeypatch.setattr(net,'attempt',lambda *args:pytest.fail('cannot verify another worker claim'))
    result=room_matches.verify(net,'game',agent,'func_1','int candidate;')
    assert result['pending'] and 'another worker' in result['reason']
    assert [dict(row) for row in net.store.all('SELECT * FROM claims ORDER BY function_id')]==before


def test_successful_retry_preserves_other_work_and_clears_submission(setup,monkeypatch):
    net,agent,original=setup
    key,_=room_matches.save(net,'game','host',agent,'func_1','int candidate;',None)
    def attempt(worker,name,source,slug):
        fn=net._function(name,slug)
        assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fn['id'])['agent_id']==agent.id
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?",(source,fn['id']))
        return {'matched':True,'score':0}
    monkeypatch.setattr(net,'attempt',attempt)
    monkeypatch.setattr(room_matches,'attach_baseline',lambda *args:None)
    result=room_matches.retry(net,'game',key)
    assert result['matched'] and not room_matches.pending(net,'game')
    assert [dict(row) for row in net.store.all('SELECT * FROM claims')]==[original]
