"""Hashes survive source expiry; active candidates and durable recovery evidence remain."""
import hashlib
import json

import pytest

from decompnet import payload_links
from decompnet.app import agent_tokens, database_storage, search_history, trial_bookkeeping
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


def test_miss_batch_survives_stop_and_reopen_and_identical_upsert_does_not_write(data):
    from pathlib import Path

    from decompnet.store import Store

    net, _, _, fid = data
    first = 'deep-v1:first'
    second = 'deep-v1:second'
    rich = json.dumps({'score': 42, 'rows': [['instruction' * 70]], 'output': 'fingerprint'})
    with pytest.raises(RuntimeError, match='Stop'), trial_bookkeeping.scope():
        for value in ('50', rich, rich):
            search_history.record_miss(net.store, fid, first, 'measured', value)
        search_history.record_miss(net.store, fid, second, 'other candidate', '100')
        assert trial_bookkeeping.current().statement_count == 2
        assert search_history.miss_feedback(net.store, fid, first, {}) == rich
        raise RuntimeError('Stop')
    reopened = Store(Path(net.store.path))
    try:
        assert search_history.miss_feedback(reopened, fid, first, {}, read=True) == rich
        assert search_history.miss_feedback(reopened, fid, second, {}, read=True) == '100'
        assert reopened.one("SELECT typeof(feedback) FROM main.reuse_trials WHERE signature=?", first)[0] == 'blob'
        before = reopened.conn.total_changes
        search_history.record_miss(reopened, fid, first, 'measured', rich)
        search_history.record_miss(reopened, fid, second, 'other candidate', '100')
        assert reopened.conn.total_changes == before
        search_history.record_miss(reopened, fid, first, 'new measurement', '35')
        assert search_history.miss_feedback(reopened, fid, first, {}, read=True) == '35'
    finally:
        reopened.conn.close()


def test_function_miss_cache_reads_once_and_flushes_on_stop(data, monkeypatch):
    net, _, _, fid = data
    store = net.store
    rich = json.dumps({'score': 42, 'rows': [['instruction' * 70]], 'output': 'fingerprint'})
    search_history.record_miss(store, fid, 'deep-v1:rich', 'prior turn', rich)
    with store.tx() as db:
        db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',
                   (fid, 'deep-profile-v1:failed', 'compile_failed', 'probe', '{}'))
        db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',
                   (fid, 'deep-profile-v1:exact', 'exact', 'verified', '{}'))
        db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',
                   (fid, 'deep-resume-v1', 'frontier', 'repair roots', '[1]'))

    misses_reads = []
    read = store._read

    def counted(sql, *args, **kwargs):
        if 'reuse_trials' in sql:
            misses_reads.append(sql)
        return read(sql, *args, **kwargs)

    monkeypatch.setattr(store, '_read', counted)
    log = Log()
    changes = store.conn.total_changes
    with pytest.raises(RuntimeError, match='Stop'), search_history.function_turn(log, store, fid) as misses:
        assert log.miss_cache is misses
        assert isinstance(misses.saved['deep-v1:rich'], tuple)  # compressed until needed
        assert misses.get('deep-v1:rich') == ('miss', rich)
        assert misses.get('deep-profile-v1:failed') == ('compile_failed', None)
        assert misses.get('deep-profile-v1:exact') is None
        assert misses.get('deep-resume-v1') is None
        for i in range(700):
            key = f'deep-v1:new{i}'
            assert misses.get(key) is None
            misses.record(key, 'miss', 'new turn', '100')
            assert misses.get(key) == ('miss', '100')
        misses.record('deep-profile-v1:exact', 'miss', 'stale parallel verdict', '100')
        assert len(misses_reads) == 1 and store.conn.total_changes == changes
        raise RuntimeError('Stop')
    assert log.miss_cache is None
    from decompnet.store import Store
    reopened = Store(store.path)
    try:
        row = reopened.one("SELECT COUNT(*) n FROM reuse_trials WHERE signature LIKE 'deep-v1:new%'")
        assert row['n'] == 700
        assert reopened.one("SELECT outcome FROM reuse_trials WHERE signature='deep-profile-v1:exact'")[0] == 'exact'
    finally:
        reopened.close_readers()
        reopened.conn.close()


def test_stop_flushes_misses_cursor_handoff_and_counters_in_one_transaction(data, monkeypatch):
    from decompnet.app import deep_handoff, experiment_evidence, proposal_progress
    from decompnet.app.deep_sweep import Sweeps
    from decompnet.app.support import Jobs
    from decompnet.store import Store
    from decomprepair.debug_boundary_repairs import FAMILY

    net, _, pid, fid = data
    store = net.store
    fn = dict(net._function('target', 'game'))
    proj = {'id': pid, 'arch': 'mips'}
    source = fn['best_source']
    sweeps = Sweeps(net, Jobs())
    experiment_evidence.prepare(net, pid)
    proposal_progress.prepare(net, pid)
    monkeypatch.setattr(experiment_evidence, 'build_scope', lambda *args: 'fixed build')
    monkeypatch.setattr(deep_handoff, 'smallest_block', lambda *args: [('r', 'target', 'draft')])
    pool = [(FAMILY, f'int target(void){{return {n};}}') for n in range(4)]
    baseline_scope = experiment_evidence.scope(net, proj, fn, source)
    tx, writes = store.tx, []

    def observed(*args, **kwargs):
        writes.append(1)
        return tx(*args, **kwargs)

    monkeypatch.setattr(store, 'tx', observed)
    with pytest.raises(RuntimeError, match='Stop'), search_history.function_turn(Log(), store, fid) as misses:
        for index in range(3):
            experiment_evidence.record(net, fn, baseline_scope, 'frame', source,
                                       source + str(index), 5, 5, 'same', 'same', 1)
        assert experiment_evidence.effect_rows(store, fid, baseline_scope)[0]['attempted'] == 3
        assert deep_handoff.save(net, proj, fn, source, [])
        assert not deep_handoff.brief(net, proj, fn, source)
        order, cursor = proposal_progress.order(net, proj, fn, source, pool)
        assert order == pool and cursor is not None
        for _, candidate in pool[:2]:
            cursor.measured(FAMILY, candidate, 5)
        assert proposal_progress.order(net, proj, fn, source, pool)[0][0] == pool[2]
        sweeps.record_recipes(fid, {'frame': (3, 0, 0, 0, 0, 0)}, [], project=pid)
        misses.record('deep-v1:stored', 'miss', 'test', '5')
        assert writes == []
        raise RuntimeError('Stop')

    assert writes == [1]
    reopened = Store(store.path)
    try:
        assert reopened.one('SELECT feedback FROM reuse_trials WHERE signature=?', 'deep-v1:stored')[0] == '5'
        assert reopened.one('SELECT tested FROM deep_permutation_recipes WHERE function_id=?', fid)[0] == 3
        assert reopened.one('SELECT attempted FROM repair_input_effects WHERE function_id=?', fid)[0] == 3
        assert reopened.one('SELECT last_source FROM repair_proposal_progress WHERE function_id=?', fid)[0] == hashlib.sha256(pool[1][1].encode()).hexdigest()
        handoff = reopened.one('SELECT value FROM settings WHERE key=?', 'deep-handoff:' + str(fid))
        assert json.loads(handoff[0])['families'][0]['attempted'] == 3
    finally:
        reopened.close_readers()
        reopened.conn.close()


def test_zero_day_cleanup_is_rejected_and_reference_map_can_rebuild(data):
    net,a,_,fid=data
    old=attempt(net,a,fid,'loss');attempt(net,a,fid,'latest')
    with net.store.tx() as db:
        db.execute("UPDATE attempts SET created_at=strftime('%s','now') WHERE id=?",(old,))
    with pytest.raises(ValueError,match='positive age'):
        database_storage.prune(net,{'days':0},Log())
    assert net.store.one('SELECT source FROM attempts WHERE id=?',old)[0]=='loss'
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
