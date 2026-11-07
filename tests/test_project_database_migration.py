"""Upgrade actual independent SQLite files, including failures between commits."""
import json
import sqlite3
from pathlib import Path

import pytest

from decompnet.app.agents import SCHEMA as SESSION_SCHEMA
from decompnet.app.transcripts import Transcripts, service
from decompnet.network import Network
from decompnet.project_migration import state, upgrade
from decompnet.shard_ids import BLOCK


def close(net):
    net._pool.shutdown()
    stores = {net.store,*(store for store,_ in net.project_stores())}
    if net._project_files is not None:stores.update(net._project_files.opened.values())
    transcripts = getattr(net,'_transcript_service',None)
    if transcripts is not None:stores.update(item.store for item in transcripts.stores.values())
    for store in stores:
        reader = getattr(store._local,'reader',None)
        if reader is not None:reader.conn.close()
        store.conn.close()


def legacy(path):
    net = Network(path)
    with net.store.tx() as db:
        for sql in SESSION_SCHEMA:db.execute(sql)
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(1,'worker','token')")
        for pid,slug in ((1,'first'),(2,'second')):
            db.execute("INSERT INTO projects(id,slug,name,root,config) VALUES(?,?,?,'.','{}')",(pid,slug,slug))
            db.execute("INSERT INTO functions(id,project_id,name,n_insns,target,relocs,asm,best_source) VALUES(?,?,'function',1,?,'[]','nop',?)",
                       (10*pid,pid,b'\0'*4,('source '+slug+'\n')*1000))
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,?,'')",(pid,'int '+slug+';'))
            db.execute('INSERT INTO attempts(id,function_id,agent_id,source,score,compiled,created_at) VALUES(?,?,1,?,10,1,1)',
                       (pid,10*pid,('attempt '+slug+'\n')*1000))
            db.execute('INSERT INTO notes(project_id,function_id,text) VALUES(?,?,?)',(pid,10*pid,slug+' note'))
            db.execute("INSERT INTO helper_sessions(id,helper_id,project,kind,model,agent,started_at) VALUES(?,?,?,'codex','test','worker',1)",(pid,pid,slug))
            db.execute('INSERT INTO settings VALUES(?,?)',('agent-review:'+str(10*pid),json.dumps({'owner':slug})))
            db.execute('INSERT INTO settings VALUES(?,?)',('session-efficiency:'+str(pid),json.dumps({'project':slug})))
        db.execute("INSERT INTO settings VALUES('helper-message-id-floor','100')")
        db.execute("INSERT INTO settings VALUES('agent-prompt-overrides','{}')")
    transcripts = Transcripts(net.store)
    for pid in (1,2):transcripts.append(pid,'text',('conversation '+str(pid)+'\n')*1000)
    close(net)


def test_upgrade_moves_operations_and_transcripts_preserving_exact_payloads(tmp_path):
    path = tmp_path/'catalog.db';legacy(path)
    net = Network(path,project_databases=True)
    try:
        assert state(net.store)['phase']=='ready'
        assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==0
        assert net.store.one('SELECT COUNT(*) n FROM helper_messages')['n']==0
        assert net.store.one('SELECT COUNT(*) n FROM text_payloads')['n']==0
        assert net.store.one("SELECT value FROM settings WHERE key='agent-prompt-overrides'")
        for pid,slug in ((1,'first'),(2,'second')):
            store = net.project_store(slug)
            assert store is not net.store
            assert store.one('SELECT COUNT(*) n FROM projects')['n']==1
            assert store.one('SELECT COUNT(*) n FROM functions')['n']==1
            assert store.one('SELECT best_source FROM functions')['best_source']==('source '+slug+'\n')*1000
            assert store.one('SELECT header FROM context_versions')['header']=='int '+slug+';'
            assert store.one('SELECT source FROM attempts')['source']==('attempt '+slug+'\n')*1000
            assert store.one('SELECT value FROM settings WHERE key=?','agent-review:'+str(10*pid))
            assert not store.one('SELECT value FROM settings WHERE key=?','agent-review:'+str(10*(3-pid)))
            transcript = service(net).for_project(slug)
            assert transcript.store is not store
            assert not transcript.store.one("SELECT 1 FROM sqlite_master WHERE name='functions'")
            assert transcript.read(slug,pid)['events'][0]['text']==('conversation '+str(pid)+'\n')*1000
            transcript.append(pid,'text','new exchange')
            with store.tx() as db:
                identity = db.execute("INSERT INTO functions(project_id,name) VALUES(?,'new')",(pid,)).lastrowid
            assert identity==pid*BLOCK+1
            assert store.one('PRAGMA foreign_key_check') is None
        assert Path(str(path)+'.before-project-databases.sqlite3').is_file()
    finally:close(net)
    restarted = Network(path)
    try:assert restarted.project_store('second').one('SELECT COUNT(*) n FROM functions')['n']==2
    finally:close(restarted)


@pytest.mark.parametrize('phase',['fenced','copied:1','copies-ready','retiring','published'])
def test_interrupted_upgrade_resumes_without_lost_or_duplicated_records(tmp_path,phase):
    path = tmp_path/'catalog.db';legacy(path)
    net = Network(path)
    def fault(current):
        if current==phase:raise RuntimeError('simulated interruption')
    with pytest.raises(RuntimeError,match='simulated'):
        upgrade(net,log=lambda *_:None,fault=fault)
    net._pool.shutdown();net.store.conn.close()
    restored = Network(path)
    try:
        assert state(restored.store)['phase']=='ready'
        for pid in (1,2):
            assert restored.project_store(pid).one('SELECT COUNT(*) n FROM attempts')['n']==1
            transcript = service(restored).for_project(pid)
            assert transcript.store.one('SELECT COUNT(*) n FROM helper_messages')['n']==1
    finally:close(restored)


def test_missing_project_file_never_opens_empty_database_or_falls_back(tmp_path):
    path = tmp_path/'catalog.db';legacy(path)
    net = Network(path,project_databases=True)
    selected = net.project_store(1).path
    close(net)
    Path(selected).rename(str(selected)+'.missing')
    with pytest.raises(ValueError,match='database is missing'):Network(path)
    assert not Path(selected).exists()


def test_old_operational_writers_are_fenced_and_original_backup_is_readable(tmp_path):
    path = tmp_path/'catalog.db';legacy(path)
    stale = sqlite3.connect(path)
    net = Network(path,project_databases=True)
    try:
        with pytest.raises(sqlite3.OperationalError,match='storage_migration_admin'):
            stale.execute("INSERT INTO functions(project_id,name) VALUES(1,'stale')")
        stale.rollback()
        with pytest.raises(sqlite3.IntegrityError,match='own database'):
            with net.store.tx() as db:db.execute("INSERT INTO functions(project_id,name) VALUES(1,'wrong-store')")
        with sqlite3.connect(str(path)+'.before-project-databases.sqlite3') as db:
            assert db.execute('SELECT COUNT(*) FROM functions').fetchone()[0]==2
    finally:stale.close();close(net)


def values(slug):
    return dict(slug=slug,name=slug,description='test',arch='mips',big_endian=False,root='.',config={})


def test_new_project_publication_rollback_recovery_and_nonrecycled_ids(tmp_path,monkeypatch):
    path = tmp_path/'catalog.db'
    net = Network(path,project_databases=True)
    with pytest.raises(RuntimeError,match='cancelled'):
        with net.project_registration(**values('cancelled')) as (db,pid):
            db.execute("INSERT INTO functions(project_id,name) VALUES(?,'cancelled')",(pid,))
            raise RuntimeError('cancelled')
    assert not net.store.one('SELECT 1 FROM projects')
    with net.project_registration(**values('first')) as (db,pid):
        db.execute("INSERT INTO functions(project_id,name) VALUES(?,'first')",(pid,))
    assert pid==2  # A cancelled reservation is never reused.
    assert net.project_store('first').one('SELECT id FROM functions')['id']==2*BLOCK+1
    original = net._project_files._publish
    monkeypatch.setattr(net._project_files,'_publish',lambda *args:(_ for _ in ()).throw(RuntimeError('crash before publication')))
    with pytest.raises(RuntimeError,match='before publication'):
        with net.project_registration(**values('recover')) as (db,pid):
            db.execute("INSERT INTO functions(project_id,name) VALUES(?,'recover')",(pid,))
    assert not net.store.one("SELECT 1 FROM projects WHERE slug='recover'")
    monkeypatch.setattr(net._project_files,'_publish',original)
    net._project_files.recover()
    assert net.project_store('recover').one('SELECT name FROM functions')['name']=='recover'
    removed = net.project_store('recover')
    assert net.remove_project('recover')==1
    assert not net.store.one("SELECT 1 FROM projects WHERE slug='recover'")
    with pytest.raises(sqlite3.IntegrityError,match='own database'):
        with removed.tx() as db:db.execute("INSERT INTO functions(project_id,name) VALUES(?,'late')",(pid,))
    with net.project_registration(**values('another')) as (db,new_id):pass
    assert new_id>pid
    close(net)
    restarted = Network(path)
    try:
        assert restarted.project_store('first').one('SELECT name FROM functions')['name']=='first'
        assert restarted.project_store('another').one('SELECT id FROM projects')['id']==new_id
    finally:close(restarted)


def test_upgrade_resumes_rejecting_modified_copy_or_missing_transcripts(tmp_path):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path)
    def fault(phase):
        if phase=='copies-ready':raise RuntimeError('pause')
    with pytest.raises(RuntimeError):upgrade(net,log=lambda *_:None,fault=fault)
    target=tmp_path/state(net.store)['projects']['1']
    with sqlite3.connect(target) as db:db.execute("UPDATE functions SET name='changed'")
    with pytest.raises(ValueError,match='changed before publication'):upgrade(net,log=lambda *_:None)
    assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==2
    net._pool.shutdown();net.store.conn.close()


def test_migrated_room_progress_reuse_and_notifications_use_real_router(tmp_path):
    from decompnet.app import saves
    from decompnet.app.activity_index import sync
    from decompnet.app.engine.gui import GuiMixin
    from decompnet.app.reuse_donors import Donors
    from decompnet.app.twins import Print, Twins
    from decompnet.network import Agent
    from tests.test_project_storage import _room_projects, _scoped_room
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    try:
        stores={pid:net.project_store(pid) for pid in (1,2)}
        _room_projects(net,stores,tmp_path)
        for pid in (1,2):
            with stores[pid].tx() as db:
                db.execute('UPDATE functions SET size=64')
                cfg=json.loads(db.execute('SELECT config FROM projects').fetchone()['config'])
                cfg['compiler']={'toolchain':'test','flags':[]}
                db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
        net.project_row=net._project;net.fork_slug=lambda _:None
        actor=Agent(1,'worker',None)
        net._record(net._function('function','first'),actor,0,'verified first',1,matched=True)
        net._record(net._function('function','second'),actor,20,'second draft',1)
        room=_scoped_room(net,'first')
        snapshot=room.state(0,0,0,actor)
        assert snapshot['matched'][0]['matched_source']=='verified first'
        assert not _scoped_room(net,'second').state(0,0,0,actor)['matched']
        assert net.progress.sync('first',force=True)
        assert (saves.default_folder(net,'first')/'src/function.c').is_file()
        from decompnet.app.engine.briefs import BriefMixin
        net._layout_key=BriefMixin._layout_key.__get__(net)
        twins=Twins(net);twins.prints=lambda proj:{proj['id']*10:Print('same',[],64)}
        net.twins=twins
        assert twins.index_project(net._project('first'))==1
        donors=Donors(net,net._project('second')).candidates(64,32,128,'','',5)
        assert donors[0]['project_id']==1
        # A newer event from a lower project-ID range must still be delivered.
        with stores[2].tx() as db:net._event(db,2,1,'match','second notification')
        sync(net,drain=True)
        old=net.store.one('SELECT MAX(id) n FROM activity_events')['n']
        with stores[1].tx() as db:
            db.execute("UPDATE projects SET name='Current first title'")
            net._event(db,1,1,'match','first newer notification')
        rows=GuiMixin.events_after(net,old)
        assert any(row['detail']=='first newer notification' and row['project']=='Current first title' for row in rows['events'])
        assert not net.store.one('SELECT 1 FROM functions')
    finally:close(net)


def test_real_engine_registers_imported_project_in_own_file_and_reopens(tmp_path):
    from decompnet.app.engine import RomNetwork
    net=RomNetwork(tmp_path/'catalog.db',tmp_path,project_databases=True)
    root=tmp_path/'checkout';root.mkdir()
    net.forks._register('imported','Imported','local',root,{'kind':'fork','platform':'n64','ready':False},'imported')
    store=net.project_store('imported')
    assert store is not net.store
    assert store.one('SELECT COUNT(*) n FROM context_versions')['n']==1
    assert net.store.one('SELECT COUNT(*) n FROM context_versions')['n']==0
    close(net)
    reopened=RomNetwork(tmp_path/'catalog.db',tmp_path)
    try:
        assert reopened.project_store('imported').one('SELECT name FROM projects')['name']=='Imported'
        assert reopened.summary('imported')['slug']=='imported'
    finally:close(reopened)


@pytest.mark.parametrize('kind',['missing-transcript','wrong-transcript','empty-project'])
def test_missing_or_wrong_storage_identity_never_creates_replacement_data(tmp_path,kind):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    selected=Path(net.project_store(1).path)
    close(net)
    if kind=='missing-transcript':Path(str(selected)+'.transcripts.sqlite3').unlink()
    elif kind=='wrong-transcript':
        with sqlite3.connect(str(selected)+'.transcripts.sqlite3') as db:
            db.execute("UPDATE settings SET value='2' WHERE key='transcript-project-id'")
    else:
        selected.unlink()
        selected.touch()
    with pytest.raises((ValueError,sqlite3.DatabaseError)):
        net=Network(path)
        try:service(net).for_project('first')
        finally:close(net)
    if kind=='missing-transcript':assert not Path(str(selected)+'.transcripts.sqlite3').exists()
    if kind=='empty-project':assert selected.stat().st_size==0


def test_concurrent_recovery_waits_for_active_project_registration(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    net=Network(tmp_path/'catalog.db',project_databases=True)
    waiting=threading.Event()
    def recover():
        waiting.set()
        net._project_files.recover()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with net.project_registration(**values('active')) as (db,pid):
                db.execute("INSERT INTO functions(project_id,name) VALUES(?,'in progress')",(pid,))
                future=pool.submit(recover)
                assert waiting.wait(2)
                assert not future.done()
            future.result(timeout=5)
        assert net.project_store('active').one('SELECT name FROM functions')['name']=='in progress'
    finally:close(net)


def test_removed_project_recovery_finishes_after_fencing(tmp_path,monkeypatch):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    monkeypatch.setattr(net._project_files,'_unpublish',lambda *a:(_ for _ in ()).throw(RuntimeError('crash after fence')))
    with pytest.raises(RuntimeError):net.remove_project('first')
    # Close known handles directly: public selection correctly rejects removed state.
    net._pool.shutdown()
    for store in net._project_files.opened.values():store.conn.close()
    net.store.conn.close()
    restored=Network(path)
    try:
        assert not restored.store.one("SELECT 1 FROM projects WHERE slug='first'")
        assert restored.project_store('second').one('SELECT COUNT(*) n FROM functions')['n']==1
    finally:close(restored)


def test_unknown_schema_stops_before_freezing_or_moving_original_rows(tmp_path):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path)
    try:
        with net.store.tx() as db:db.execute('CREATE TABLE unknown_extension(id INTEGER PRIMARY KEY,secret TEXT)')
        with pytest.raises(ValueError,match='ownership rule'):upgrade(net,log=lambda *_:None)
        assert state(net.store) is None
        with net.store.tx() as db:db.execute("UPDATE functions SET size=4 WHERE id=10")
        assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==2
    finally:close(net)


def test_api_resume_conversation_is_stored_with_transcripts_after_upgrade(tmp_path):
    from types import SimpleNamespace

    from decompnet.app import session_recovery
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    try:
        session=SimpleNamespace(id='1',session_id=1,slug='first',options={})
        messages=[{'role':'user','content':'continue'},{'role':'assistant','content':'saved reply'}]
        session_recovery.api_messages(net,session,'same endpoint',messages)
        assert session_recovery.api_messages(net,session,'same endpoint')==messages
        assert session_recovery.api_messages(net,session,'other endpoint') is None
        assert not net.project_store('first').one("SELECT 1 FROM settings WHERE key='helper-conversation:1'")
        assert service(net).for_project('first').store.one("SELECT 1 FROM settings WHERE key='helper-conversation:1'")
    finally:close(net)


def test_other_project_attempt_can_commit_with_catalog_and_first_project_busy(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.network import Agent
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    held,released=threading.Event(),threading.Event()
    def hold_first():
        with net.project_store(1).tx():
            held.set()
            assert released.wait(5)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder=pool.submit(hold_first)
            assert held.wait(2)
            try:
                with net.store.tx():
                    result=pool.submit(net._record,net._function('function','second'),Agent(1,'worker',None),9,'independent',1)
                    result.result(timeout=3)
            finally:released.set()
            holder.result(timeout=3)
        assert net.project_store(2).one('SELECT COUNT(*) n FROM attempts')['n']==2
        assert net.project_store(1).one('SELECT COUNT(*) n FROM attempts')['n']==1
    finally:close(net)


def test_import_discovery_uses_current_project_paths_and_configuration(tmp_path):
    from decompnet.app.forkengine import ForkEngine
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    net.workspace=tmp_path
    root=tmp_path/'moved-checkout';root.mkdir()
    (root/'.git').mkdir()
    try:
        with net.project_store('first').tx() as db:
            db.execute('UPDATE projects SET root=?,config=?',(str(root),json.dumps({'repo_url':str(root),'kind':'fork'})))
        row=next(p for p in net.project_rows() if p['slug']=='first')
        assert row['root']==str(root)
        assert net.store.one("SELECT root FROM projects WHERE slug='first'")['root']=='.'
        assert ForkEngine(net)._local_roots()[str(root.resolve())]=='first'
    finally:close(net)


def test_api_claim_checks_and_boundary_updates_use_migrated_owner(tmp_path):
    import time
    from types import SimpleNamespace

    from decompnet.agents.api import _holds_claim
    from decompnet.app import boundary_review
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    try:
        store=net.project_store(1)
        with store.tx() as db:db.execute('INSERT INTO claims VALUES(10,1,?,?)',(time.time(),time.time()+60))
        assert _holds_claim(net,1,'first')
        assert not _holds_claim(net,1,'second')
        assert boundary_review.refresh(net,net._project('first'),SimpleNamespace(boundary_reason=lambda _: 'Review this target'))==1
        assert json.loads(store.one('SELECT meta FROM functions')['meta'])['boundary_review']=='Review this target'
        assert not net.project_store(2).one('SELECT meta FROM functions')['meta']
        assert net.store.one('SELECT COUNT(*) n FROM claims')['n']==0
    finally:close(net)


def test_imported_reuse_reads_donor_and_records_destination_after_migration(tmp_path,monkeypatch):
    from decompnet.app import shared_reuse
    from decompnet.app.engine.core import Core
    from decompnet.app.twins import Print, Twins
    from tests.test_project_storage import _room_projects
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    try:
        stores={pid:net.project_store(pid) for pid in (1,2)}
        _room_projects(net,stores,tmp_path)
        net.project_row=net._project;net.local_agent=Core.local_agent.__get__(net)
        net.fork_slug=lambda slug:slug if slug=='second' else None
        with stores[1].tx() as db:db.execute("UPDATE functions SET status='matched',matched_source='int function(void){return 0;}'")
        twins=Twins(net)
        twins.prints=lambda proj:{proj['id']*10:Print('same',[],64)}
        twins._fork_names=lambda *args:{'function':'function'}
        seen=[]
        def judge(actor,proj,a,b,source,**kwargs):
            seen.append((proj['id'],a['project_id'],b['project_id']))
            return 'miss','judge did not accept this candidate'
        twins._attempt_twin=judge
        snapshot={'blocked':0,'hints':{20:[{'verified':True,'source_slug':'first','function_id':10}]}}
        monkeypatch.setattr(shared_reuse,'discover',lambda *args,**kwargs:snapshot)
        assert shared_reuse.transfer(twins,net._project('second'),None,snapshot=snapshot)==0
        assert seen==[(2,1,2)]
        assert stores[2].one('SELECT source_project_id,outcome FROM cross_game_attempts')['outcome']=='miss'
        assert stores[2].one('SELECT 1 FROM twin_misses')
        assert not stores[1].one('SELECT 1 FROM cross_game_attempts')
        assert not net.store.one('SELECT 1 FROM cross_game_attempts')
    finally:close(net)


def test_sdk_naming_reads_assembly_targets_from_migrated_project(tmp_path):
    from types import SimpleNamespace

    from decompnet.app.sdk_identity import naming_prints
    from decompnet.rom.symbols import SymbolTable
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path,project_databases=True)
    try:
        net.cfg=lambda proj:{'kind':'rom','big_endian':True}
        net.symbols=lambda pid:SymbolTable()
        net.twins=SimpleNamespace(prints=lambda proj:{})
        with net.project_store(1).tx() as db:db.execute("UPDATE functions SET status='asm'")
        assert set(naming_prints(net,net._project('first')))=={10}
        assert naming_prints(net,net._project('second'))=={}
    finally:close(net)


def test_upgrade_preserves_resume_payload_and_never_launched_helper_id_reservation(tmp_path):
    path=tmp_path/'catalog.db';legacy(path)
    old=Network(path)
    payload=json.dumps({'identity':'same endpoint','messages':[{'role':'user','content':'saved context'}]})
    with old.store.tx() as db:
        db.execute("INSERT INTO helpers(id,project,kind,name,label,state) VALUES(999,'first','codex','reserved-worker','reserved','paused')")
        db.execute("INSERT INTO settings VALUES('helper-conversation:1',?)",(payload,))
    close(old)
    net=Network(path,project_databases=True)
    try:
        assert int(net.store.one("SELECT value FROM settings WHERE key='helper-id-floor'")['value'])>=999
        assert service(net).for_project('first').store.one("SELECT value FROM settings WHERE key='helper-conversation:1'")['value']==payload
        assert not net.project_store(1).one("SELECT 1 FROM settings WHERE key='helper-conversation:1'")
        assert not net.project_store(2).one("SELECT 1 FROM settings WHERE key='helper-conversation:1'")
    finally:close(net)


def test_upgrade_supports_database_paths_with_uri_special_characters(tmp_path):
    path=tmp_path/'catalog #1.db';legacy(path)
    net=Network(path,project_databases=True)
    try:assert net.project_store('first').one('SELECT COUNT(*) n FROM functions')['n']==1
    finally:close(net)


def test_upgrade_applies_legacy_session_columns_before_usage_backfill(tmp_path):
    path=tmp_path/'catalog.db';legacy(path)
    old=Network(path)
    with old.store.tx() as db:
        db.execute('ALTER TABLE helper_sessions DROP COLUMN task')
        db.execute("INSERT INTO helpers(id,project,kind,name,label,state,options) VALUES(1,'first','codex','worker','worker','paused',?)",
                   (json.dumps({'task':'readable'}),))
    close(old)
    net=Network(path,project_databases=True)
    try:
        row=net.project_store('first').one('SELECT task,usage_coverage FROM helper_sessions')
        assert row['task']=='readable' and row['usage_coverage']=='unknown'
        assert net.project_store('second').one('SELECT task FROM helper_sessions')['task']==''
    finally:close(net)


def test_snapshot_and_fence_also_exclude_raw_sqlite_writers(tmp_path):
    path=tmp_path/'catalog.db';legacy(path)
    net=Network(path)
    checked=[]
    def fault(phase):
        if phase!='snapshot-locked':return
        external=sqlite3.connect(path,timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                external.execute("UPDATE functions SET name='racing writer' WHERE id=10")
            checked.append(True)
        finally:external.close()
    upgrade(net,log=lambda *_:None,fault=fault)
    assert checked==[True]
    backup=sqlite3.connect(str(path)+'.before-project-databases.sqlite3')
    try:
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'project_store_guard_%'").fetchone()
        assert not backup.execute("SELECT 1 FROM settings WHERE key='project-databases-v1'").fetchone()
    finally:backup.close()
    close(net)
    restored=Network(path)
    try:assert restored.project_store(1).one('SELECT name FROM functions')['name']=='function'
    finally:close(restored)


def test_save_and_restore_initialize_knowledge_in_new_project_database(tmp_path):
    from decompnet.app import saves
    from decompnet.app.engine import RomNetwork
    net=RomNetwork(tmp_path/'catalog.db',tmp_path,project_databases=True)
    root=tmp_path/'checkout';root.mkdir()
    net.forks._register('imported','Imported','local',root,{'kind':'fork','platform':'n64','ready':False},'imported')
    store=net.project_store('imported')
    assert store is not net.store
    assert store.one("SELECT name FROM sqlite_master WHERE name='knowledge'") is None
    try:
        manifest,_,_=saves._manifest_from_rows(net,net.project_row('imported'),net.cfg(net.project_row('imported')),[],root)
        assert manifest['knowledge'] == []
        assert store.one("SELECT name FROM sqlite_master WHERE name='knowledge'") is not None
        with store.tx() as db:
            db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(?,?,'accepted',1)",
                       (net.project_row('imported')['id'],'Preserved knowledge'))
        manifest,_,_=saves._manifest_from_rows(net,net.project_row('imported'),net.cfg(net.project_row('imported')),[],root)
        assert manifest['knowledge'] == ['Preserved knowledge']
    finally:close(net)
