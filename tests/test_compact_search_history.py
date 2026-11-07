"""Hashes survive source expiry; active candidates and durable recovery evidence remain."""
import hashlib
import json

import pytest

from decompnet import payload_links
from decompnet.app import agent_tokens, database_storage, search_history
from decompnet.network import Network


class Log:
    def __call__(self,line):pass
    def progress(self,value):pass
    def check_cancel(self):pass


@pytest.fixture
def data(tmp_path):
    net=Network(tmp_path/'state.db')
    agent=net.authenticate(net.register_agent('worker'))
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('game','/scratch','{}')").lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name,best_source,best_score) VALUES(?,'target','winner',5)",(pid,)).lastrowid
    return net,agent,pid,fid


def attempt(net,agent,fid,source,score=10,improved=0):
    with net.store.tx() as db:
        return db.execute('INSERT INTO attempts(function_id,agent_id,source,score,compiled,score_improved,created_at) VALUES(?,?,?,?,?,?,0)',
                          (fid,agent.id,source,score,int(score is not None),improved)).lastrowid


def test_hash_only_expiry_keeps_scores_latest_and_improvements(data):
    net,a,pid,fid=data
    old=attempt(net,a,fid,'old loss')
    with net.store.tx() as db:
        db.execute("INSERT INTO agent_family_targets VALUES(?,?,'compiled-miss',?)",(fid,'original inputs',hashlib.sha256(b'old loss').hexdigest()))
    good=attempt(net,a,fid,'historical improvement',8,1)
    exact=attempt(net,a,fid,'historical exact',0)
    latest_ok=attempt(net,a,fid,'latest compiled')
    latest_error=attempt(net,a,fid,'latest failed',None)
    with net.store.tx() as db:
        db.execute('INSERT INTO attempt_hints VALUES(?,?,?)',(old,fid,'This did not improve the score'))
    result=database_storage.prune(net,{'days':1},Log())
    assert result['removed_sources']==1
    row=net.store.one('SELECT * FROM attempts WHERE id=?',old)
    assert row['source'] is None and row['source_hash'] is None
    assert row['source_digest']==hashlib.sha256(b'old loss').hexdigest()
    assert agent_tokens.compiled_before(net,{'id':fid,'project_id':pid},'old loss',scope='original inputs')
    assert not agent_tokens.compiled_before(net,{'id':fid,'project_id':pid},'old loss',scope='changed header')
    assert row['score']==10 and row['compiled']==1 and row['score_improved']==0
    for aid in (good,exact,latest_ok,latest_error):
        assert net.store.one('SELECT source FROM attempts WHERE id=?',aid)['source']
    # Briefs do not need to inflate a single source to summarize failed approaches.
    reader=net.store._reader()
    reader.create_function('payload_read',2,lambda *_:(_ for _ in ()).throw(AssertionError('decoded C')))
    brief=agent_tokens.history(net,{'id':fid,'project_id':pid})
    assert hashlib.sha256(b'latest failed').hexdigest()[:12] in brief
    assert net.store.one('SELECT summary FROM attempt_hints WHERE attempt_id=?',old)[0]=='This did not improve the score'


def test_pinned_attempt_and_legacy_unknown_improvement_survive(data):
    net,a,_,fid=data
    pinned=attempt(net,a,fid,'referenced')
    unknown=attempt(net,a,fid,'unknown',10,None)
    attempt(net,a,fid,'latest')
    with net.store.tx() as db:
        db.execute('CREATE TABLE future_evidence(id INTEGER PRIMARY KEY,attempt_id INTEGER REFERENCES attempts(id))')
        db.execute('INSERT INTO future_evidence(attempt_id) VALUES(?)',(pinned,))
    database_storage.prune(net,{'days':1},Log())
    for aid in (pinned,unknown):assert net.store.one('SELECT source FROM attempts WHERE id=?',aid)[0]


def test_digest_tracks_edits_and_survives_payload_removal(data):
    net,a,_,fid=data;aid=attempt(net,a,fid,'first')
    with net.store.tx() as db:db.execute('UPDATE attempts SET source=? WHERE id=?',('edited',aid))
    assert net.store.one('SELECT source_digest FROM main.attempts WHERE id=?',aid)[0]==hashlib.sha256(b'edited').hexdigest()
    with net.store.tx() as db:db.execute('UPDATE attempts SET source=NULL,source_hash=NULL WHERE id=?',(aid,))
    assert net.store.one('SELECT source_digest FROM main.attempts WHERE id=?',aid)[0]==hashlib.sha256(b'edited').hexdigest()


def test_gc_backfill_cancellation_and_racing_reference_changes(data,monkeypatch):
    net,a,_,fid=data
    first=attempt(net,a,fid,'first');second=attempt(net,a,fid,'second')
    # Simulate a pre-upgrade database whose reference rows have not been populated.
    with net.store.tx() as db:db.execute('DELETE FROM payload_links')
    monkeypatch.setattr(payload_links,'BATCH',1)
    log=Log();calls=[]
    def cancel():
        calls.append(1)
        if len(calls)>3:raise RuntimeError('cancel')
    log.check_cancel=cancel
    with pytest.raises(RuntimeError,match='cancel'):database_storage.collect(net,log)
    assert net.store.one('SELECT source FROM attempts WHERE id=?',first)[0]=='first'
    database_storage.collect(net,Log())
    # Triggers must follow the final packed value, regardless of nested pack-trigger order.
    with net.store.tx() as db:db.execute('UPDATE attempts SET source=? WHERE id=?',('replacement',second))
    assert database_storage.collect(net,Log())==1
    assert net.store.one('SELECT source FROM attempts WHERE id=?',second)[0]=='replacement'
    with net.store.tx() as db:db.execute('DELETE FROM attempts WHERE id=?',(first,))
    assert database_storage.collect(net,Log())==1


def test_oversized_deep_feedback_keeps_negative_cache_and_other_search_evidence(data):
    net,_,_,fid=data
    large={'score':42,'output':'hash','rows':[['assembly'*1000]]*20}
    with net.store.tx() as db:
        for key in ('deep-v1:hash','local-source-review:hash'):
            db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',(fid,key,'miss','already checked',json.dumps(large)))
    assert search_history.prune(net.store,Log())==1
    row=net.store.one("SELECT * FROM reuse_trials WHERE signature='deep-v1:hash'")
    assert json.loads(row['feedback'])=={'score':42,'output':'hash'}
    assert row['outcome']=='miss' and row['reason']=='already checked'
    assert json.loads(net.store.one("SELECT feedback FROM reuse_trials WHERE signature='local-source-review:hash'")[0])==large
    assert search_history.prune(net.store,Log())==0


def test_zero_day_cleanup_and_reference_map_recreation(data):
    net,a,_,fid=data
    old=attempt(net,a,fid,'loss');attempt(net,a,fid,'latest')
    with net.store.tx() as db:
        db.execute("UPDATE attempts SET created_at=strftime('%s','now') WHERE id=?",(old,))
    assert database_storage.prune(net,{'days':0},Log())['removed_sources']==1
    with net.store.tx() as db:
        for suffix in ('insert','update','delete'):
            db.execute('DROP TRIGGER payload_link_attempts_source_hash_'+suffix)
        db.execute("DELETE FROM payload_links WHERE owner='attempts.source_hash'")
    # Reinstallation invalidates the old completed backfill cursor.
    with net.store.tx() as db:payload_links.install(db,'attempts','source_hash')
    database_storage.collect(net,Log())
    assert net.store.one('SELECT source FROM attempts ORDER BY id DESC LIMIT 1')[0]=='latest'


def test_replace_and_primary_key_changes_do_not_leave_orphan_payloads(data):
    net,_,pid,fid=data
    with net.store.tx() as db:
        db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',(fid,'key','miss','test','first'*1000))
    database_storage.collect(net,Log())
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO reuse_trials VALUES(?,?,?,?,?)',(fid,'key','miss','test','second'*1000))
        db.execute('UPDATE functions SET best_source=? WHERE id=?',('retained'*1000,fid))
        db.execute('UPDATE functions SET id=id+100 WHERE id=?',(fid,))
    assert database_storage.collect(net,Log())==1
    assert net.store.one('SELECT best_source FROM functions WHERE project_id=?',pid)[0]=='retained'*1000
    assert net.store.one('SELECT feedback FROM reuse_trials')[0]=='second'*1000


def test_lazy_evidence_table_created_during_collection_gets_backfilled(data):
    import zlib

    from decompnet.large_text_storage import install
    net,_,_,_=data
    text='preserve lazy evidence'*1000;digest=hashlib.sha256(text.encode()).hexdigest()
    with net.store.tx() as db:
        db.execute('INSERT INTO text_payloads VALUES(?,?,?)',(digest,zlib.compress(text.encode()),len(text.encode())))
    class RacingLog(Log):
        added=False
        def progress(self,value):
            if self.added or value['phase']!='Collecting unused shared text':return
            self.added=True
            with net.store.tx() as db:
                db.execute('CREATE TABLE verified_matches(function_id INTEGER PRIMARY KEY,details TEXT,source TEXT,header TEXT)')
                db.execute('INSERT INTO verified_matches VALUES(1,?,?,?)',('{}',bytes.fromhex(digest),''))
                install(db,('verified_matches',))
    assert database_storage.collect(net,RacingLog())==0
    assert net.store.one('SELECT source FROM verified_matches')[0]==text


def test_transcript_cleanup_releases_shared_message_payloads(tmp_path):
    from decompnet.app import database_cleanup
    from decompnet.app.agents import Manager
    from decompnet.app.engine import RomNetwork
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    manager=Manager(net,tmp_path,'http://127.0.0.1/mcp')
    with net.store.tx() as db:
        sid=db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at) VALUES(999,'game','codex','model','removed',0,1)").lastrowid
    manager.transcripts.append(sid,'text','a long old transcript '*1000)
    assert net.store.one('SELECT COUNT(*) FROM text_payloads')[0]>0
    database_cleanup.run(net,{'removed':True},Log())
    assert not net.store.one('SELECT 1 FROM text_payloads')
