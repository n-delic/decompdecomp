"""Transcript cleanup preserves live work and summaries; cancellation leaves resumable batches."""
from types import SimpleNamespace

import pytest

from decompnet.app import database_cleanup as cleanup
from decompnet.app.agents import Manager
from decompnet.app.engine import RomNetwork


@pytest.fixture
def data(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    manager=Manager(net,tmp_path,'http://127.0.0.1/mcp')
    manager._record('game','codex','current','Current',{})
    with net.store.tx() as db:
        ids=[]
        for helper,name,end in [(1,'current',10),(1,'current',None),(2,'removed',20),(1,'legacy',20),(1,'current',2000000000)]:
            sid=db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at,usd,tokens) "
                           "VALUES(?,'game','codex','model',?,0,?,2,100)",(helper,name,end)).lastrowid
            ids.append(sid)
            db.execute("INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(?,0,'text','private text')",(sid,))
    return net,manager,ids


class Log:
    def __init__(self):self.snapshots=[];self.lines=[];self.cancel=False
    def __call__(self,line):self.lines.append(line)
    def progress(self,value):self.snapshots.append(dict(value))
    def check_cancel(self):
        if self.cancel:raise RuntimeError('Cancelled')


def test_removed_helper_cleanup_respects_current_identity_and_preserves_costs(data):
    net,manager,ids=data;log=Log()
    # A legacy identity sharing the current helper's ID is removed, the current identity is not.
    assert cleanup.summary(net,{'removed':True,'before':''})['sessions']==2
    result=cleanup.run(net,{'removed':True,'before':''},log)
    assert result=={'sessions':2,'messages':2}
    assert {r['session_id'] for r in net.store.all('SELECT session_id FROM helper_messages')}=={ids[0],ids[1],ids[4]}
    assert net.store.one('SELECT SUM(tokens) n FROM helper_sessions')['n']==500
    assert log.snapshots[-1]['done']==log.snapshots[-1]['total']==2
    manager.transcripts.append(ids[2],'text','late old callback')
    assert not net.store.one('SELECT id FROM helper_messages WHERE session_id=?',ids[2])


def test_cutoff_uses_completed_session_end_and_keeps_ongoing_and_newer_sessions(data):
    net,manager,ids=data
    value={'removed':False,'before':'2000-01-01'}
    assert cleanup.summary(net,value)['sessions']==3
    cleanup.save(net,value)
    cleanup.run(net,value,Log())
    assert {r['session_id'] for r in net.store.all('SELECT session_id FROM helper_messages')}=={ids[1],ids[4]}
    assert cleanup.saved(net)==dict(value,automatic=False)
    assert cleanup.summary(net,value)['messages']==0
    from decompnet.app.session_recovery import api_messages
    old=SimpleNamespace(id='1',session_id=ids[0],slug='game');new=SimpleNamespace(id='1',session_id=ids[1],slug='game')
    api_messages(net,old,'endpoint',[{'role':'assistant','content':'late old private text'}])
    assert not net.store.one("SELECT value FROM settings WHERE key='helper-conversation:1'")
    api_messages(net,new,'endpoint',[{'role':'assistant','content':'ongoing conversation'}])
    assert net.store.one("SELECT value FROM settings WHERE key='helper-conversation:1'")


def test_cancellation_is_bounded_and_partial_cleanup_can_resume(data,monkeypatch):
    net,manager,ids=data;monkeypatch.setattr(cleanup,'BATCH',2)
    for i in range(6):manager.transcripts.append(ids[2],'text',f'old {i}')
    last=net.store.one('SELECT MAX(id) n FROM helper_messages')['n']
    log=Log()
    original=log.progress
    def progress(value):
        original(value)
        if value['deleted_messages']>=2:log.cancel=True
    log.progress=progress
    with pytest.raises(RuntimeError,match='Cancelled'):cleanup.run(net,{'removed':True,'before':''},log)
    assert net.store.one('SELECT COUNT(*) n FROM helper_messages WHERE session_id=?',ids[2])['n']==5
    cleanup.run(net,{'removed':True,'before':''},Log())
    assert not net.store.one('SELECT id FROM helper_messages WHERE session_id=?',ids[2])
    manager.transcripts.append(ids[1],'text','new current event')
    assert net.store.one('SELECT MAX(id) n FROM helper_messages')['n']>last


@pytest.mark.parametrize('value',[{'removed':False,'before':''},{'removed':'yes'},{'before':'3000-01-01'},
                                 {'before':'2026-02-30'},{'before':123},None])
def test_invalid_cleanup_options_never_delete(data,value):
    net,_,_=data
    with pytest.raises(ValueError):cleanup.run(net,value,Log())
    assert net.store.one('SELECT COUNT(*) n FROM helper_messages')['n']==5


def test_automatic_cutoff_is_persisted_bounded_and_can_be_disabled(data):
    import threading
    net,_,_=data
    started=[]
    class Jobs:
        _lock=threading.RLock()
        jobs={}
        def start(self,kind,work,**kwargs):
            started.append(work);self.jobs['one']={'id':'one','kind':kind,'state':'running'};return 'one'
    jobs=Jobs();retention=net.database_retention=cleanup.Retention(net,jobs)
    value={'removed':False,'before':'2000-01-01','automatic':True}
    cleanup.save(net,value)
    assert cleanup.saved(net)==value
    retention.tick(100)
    assert len(started)==1
    retention.tick(101)
    assert len(started)==1
    retention.tick(401)  # An existing manual/automatic cleanup is shared, not duplicated.
    assert len(started)==1
    jobs.jobs['one']['state']='done'
    cleanup.save(net,dict(value,automatic=False))
    retention.tick(402)
    assert len(started)==1
    cleanup.save(net,value)
    retention.tick(403)
    assert len(started)==2
    started[-1](Log())
    jobs.jobs['one']['state']='done'
    retention.tick(704)  # No empty recurring jobs once eligible transcripts are gone.
    assert len(started)==2


def test_old_queued_run_cannot_reenable_automatic_cleanup_after_user_disables_it(data):
    net,_,_=data
    old={'removed':False,'before':'2000-01-01','automatic':True}
    cleanup.save(net,old)
    disabled=cleanup.save(net,dict(old,automatic=False))
    cleanup.run(net,old,Log())
    assert cleanup.saved(net)==disabled and not cleanup.saved(net)['automatic']


def test_cleanup_pacing_checkpoints_never_hold_the_database_write_transaction(data,monkeypatch):
    from decompnet.app import maintenance
    net,_,_=data;checks=[]
    def checkpoint():
        assert not getattr(net.store._local,'in_tx',False)
        checks.append(1)
    monkeypatch.setattr(maintenance,'checkpoint',checkpoint)
    cleanup.run(net,{'removed':True},Log())
    assert checks


def test_age_limit_rolls_forward_and_preserves_ongoing_sessions(data,monkeypatch):
    from datetime import datetime, timedelta, timezone
    net,_,ids=data
    now=datetime(2026,11,6,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now
    monkeypatch.setattr(cleanup,'datetime',Clock)
    with net.store.tx() as db:
        for sid,age in [(ids[0],15),(ids[4],13)]:
            db.execute('UPDATE helper_sessions SET ended_at=? WHERE id=?',((now-timedelta(days=age)).timestamp(),sid))
    value={'removed':False,'days':14,'automatic':True}
    cleanup.save(net,value)
    assert cleanup.saved(net)==value
    assert ids[0] in {r['id'] for r in cleanup.eligible(net,value)}
    assert ids[4] not in {r['id'] for r in cleanup.eligible(net,value)}
    now+=timedelta(days=2)
    assert ids[4] in {r['id'] for r in cleanup.eligible(net,cleanup.saved(net))}
    expected=cleanup.summary(net)['messages']
    assert cleanup.run(net,value,Log())['messages']==expected
    assert net.store.one('SELECT id FROM helper_messages WHERE session_id=?',ids[1])


@pytest.mark.parametrize('days',[0,-1,1.5,True,'14',36501])
def test_invalid_retention_ages_do_not_delete(data,days):
    net,_,_=data
    with pytest.raises(ValueError,match='whole number of days'):
        cleanup.run(net,{'days':days},Log())
    assert net.store.one('SELECT COUNT(*) n FROM helper_messages')['n']==5


def test_automatic_cleanup_rechecks_the_rolling_age(data,monkeypatch):
    from datetime import datetime, timedelta, timezone
    net,_,ids=data
    now=datetime(2026,11,6,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now
    monkeypatch.setattr(cleanup,'datetime',Clock)
    with net.store.tx() as db:
        db.execute('DELETE FROM helper_messages WHERE session_id!=?',(ids[4],))
        db.execute('UPDATE helper_sessions SET ended_at=? WHERE id=?',((now-timedelta(days=13)).timestamp(),ids[4]))
    cleanup.save(net,{'removed':False,'days':14,'automatic':True})
    started=[]
    monkeypatch.setattr(cleanup,'start',lambda *args,**kwargs:started.append(kwargs))
    retention=cleanup.Retention(net,None)
    retention.tick(0)
    assert not started
    now+=timedelta(days=2)
    retention.tick(301)
    assert started==[{'foreground':False}]
