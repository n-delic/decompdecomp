"""Exact payload storage, migration, bounded reads and conservative retention."""
import json

import pytest

from decompnet import payload_storage
from decompnet.app import database_storage
from decompnet.network import Network


class Log:
    def __init__(self):self.events=[]
    def __call__(self,line):pass
    def progress(self,value):self.events.append(value)
    def check_cancel(self):pass


@pytest.fixture
def data(tmp_path):
    net=Network(tmp_path/'db.sqlite')
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game','/scratch','{}')").lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'target')",(pid,)).lastrowid
    return net,fid


def add(net,fid,source,score=None,compiled=0):
    with net.store.tx() as db:
        return db.execute('INSERT INTO attempts(function_id,source,score,compiled,created_at) VALUES(?,?,?,?,0)',(fid,source,score,compiled)).lastrowid


def test_exact_dedup_updates_and_stable_ids(data):
    net,fid=data
    text='int café(void) { return 42; }\n'*100
    a=add(net,fid,text);b=add(net,fid,text)
    assert b==a+1
    assert net.store.one('SELECT source FROM attempts WHERE id=?',a)['source']==text
    assert net.store.one('SELECT COUNT(*) n FROM text_payloads')['n']==1
    assert net.store.one('SELECT source FROM main.attempts WHERE id=?',a)['source'] is None
    for value in (text,'changed','',None):
        with net.store.tx() as db:db.execute('UPDATE attempts SET source=? WHERE id=?',(value,a))
        assert net.store.one('SELECT source FROM attempts WHERE id=?',a)['source']==value
    assert net.store.one('SELECT source FROM attempts WHERE id=?',b)['source']==text
    with pytest.raises(RuntimeError):
        with net.store.tx() as db:
            db.execute('UPDATE attempts SET source=? WHERE id=?',('rollback',b))
            raise RuntimeError()
    assert net.store.one('SELECT source FROM attempts WHERE id=?',b)['source']==text


def test_reopen_and_raw_sql_values_are_not_rewritten(data):
    net,fid=data;a=add(net,fid,'original')
    from decompnet.store import Store
    reopened=Store(net.store.path)
    assert reopened.one('SELECT source FROM attempts WHERE id=?',a)['source']=='original'
    assert reopened.one("SELECT 'FROM attempts' value FROM attempts LIMIT 1")['value']=='FROM attempts'
    assert reopened.one('SELECT a.source FROM attempts a /* FROM attempts */ WHERE a.id=?',a)['source']=='original'


def test_legacy_pack_is_idempotent_and_cancellable(data):
    net,fid=data
    with net.store.tx() as db:
        db.execute('DROP TRIGGER pack_attempts_insert')
        a=db.execute('INSERT INTO attempts(function_id,source) VALUES(?,?)',(fid,'legacy')).lastrowid
        payload_storage.install(db)
    class Cancel(Log):
        def check_cancel(self):raise RuntimeError('cancel')
    with pytest.raises(RuntimeError):database_storage.pack(net,Cancel())
    assert net.store.one('SELECT source FROM main.attempts WHERE id=?',a)['source']=='legacy'
    assert database_storage.pack(net,Log())['packed']==1
    assert database_storage.pack(net,Log())['packed']==0
    assert net.store.one('SELECT source FROM attempts WHERE id=?',a)['source']=='legacy'


def test_repair_metadata_is_small_and_details_are_exact(data,monkeypatch):
    net,fid=data
    evidence=json.dumps({'before':{'source':'big source'*1000,'header':'huge header'*1000,'score':12},'previous':{'attempt_id':91},'diff':'large diff'*1000})
    pid=net.store.one('SELECT project_id FROM functions WHERE id=?',fid)['project_id']
    with net.store.tx() as db:
        a=db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(?,?,0,?)',(pid,fid,evidence)).lastrowid
    raw=net.store.one('SELECT evidence FROM main.repair_examples WHERE id=?',a)['evidence']
    assert len(raw)<200
    assert json.loads(raw)['before']['score']==12
    assert net.store.one('SELECT evidence FROM repair_examples WHERE id=?',a)['evidence']==evidence
    changed=json.dumps({'before':{'source':'replacement'},'previous':{'attempt_id':92}})
    with net.store.tx() as db:db.execute('UPDATE repair_examples SET evidence=? WHERE id=?',(changed,a))
    assert net.store.one('SELECT evidence FROM repair_examples WHERE id=?',a)['evidence']==changed


def test_retention_keeps_current_referenced_successful_and_latest_sources(data):
    net,fid=data
    old=add(net,fid,'discard')
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_attempt_settings VALUES(?,?)',(old,'{}'))
    best=add(net,fid,'best');seed=add(net,fid,'seed');hint=add(net,fid,'hint')
    success=add(net,fid,'successful',12,1)
    latest=add(net,fid,'latest')
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,seed=? WHERE id=?',('best','seed',fid))
        db.execute('INSERT INTO attempt_hints VALUES(?,?,?)',(hint,fid,'keep'))
    result=database_storage.prune(net,{'days':1},Log())
    assert result['removed_sources']==2
    assert net.store.one('SELECT source FROM attempts WHERE id=?',old)['source'] is None
    assert net.store.one('SELECT source_digest,source FROM attempts WHERE id=?',hint)['source'] is None
    for a in (best,seed,success,latest):assert net.store.one('SELECT source FROM attempts WHERE id=?',a)['source']
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==6
    assert net.store.one('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',old)['settings']=='{}'


def test_history_page_never_decodes_payloads_and_details_are_scoped(data,monkeypatch):
    net,fid=data
    for i in range(105):add(net,fid,str(i))
    monkeypatch.setattr(net,'_function',lambda name,slug: {'id':fid,'project_id':net.store.one('SELECT project_id FROM functions WHERE id=?',fid)[0]})
    # A metadata-only query must not invoke the SQL decompressor at all.
    net.store.conn.create_function('payload_read',2,lambda *_:(_ for _ in ()).throw(AssertionError('decoded')))
    page=database_storage.history(net,'game','target')
    assert len(page['attempts'])==100 and page['before']
    assert len(database_storage.history(net,'game','target',page['before'])['attempts'])==5
    assert page['attempts'][0]['source_available']
    with pytest.raises(ValueError):database_storage.history(net,'game','target',attempt=999999)


def test_checksum_and_missing_payload_fail_closed():
    with pytest.raises(ValueError):payload_storage.unpack('missing',None)
    import zlib
    with pytest.raises(ValueError):payload_storage.unpack('wrong',zlib.compress(b'text'))


def test_options_reject_accidental_retention():
    for rules in ({'days':-1},{'days':True},{'automatic':'yes'}):
        with pytest.raises(ValueError):database_storage.options(rules)


def test_daily_retention_is_opt_in_and_job_executes_with_cancellation_control(data):
    import threading

    from decompnet.app.database_cleanup import Retention
    net,fid=data
    add(net,fid,'old');add(net,fid,'new')
    class Jobs:
        _lock=threading.RLock()
        jobs={}
        def __init__(self):self.calls=[]
        def start(self,kind,work,**options):
            self.calls.append((kind,work,options));return 'job'
    jobs=Jobs();retention=Retention(net,jobs);net.database_retention=retention
    retention.tick(1)
    assert not jobs.calls
    database_storage.save(net,{'days':1,'automatic':True})
    retention.tick(302)
    assert len(jobs.calls)==1 and jobs.calls[0][2]['foreground'] is False
    log=Log();log.cancel_event=threading.Event()
    assert jobs.calls[0][1](log)['removed_sources']==1
    retention.tick(603)
    assert len(jobs.calls)==1
