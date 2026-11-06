"""Large-record storage preserves exact judging inputs and lazy indexed metadata reads."""
import json
import threading

import pytest

from decompnet.app import database_storage
from decompnet.app.compiler_experiments import trial_table
from decompnet.app.transcripts import Transcripts
from decompnet.large_text_storage import install
from decompnet.network import Network
from decompnet.store import Store


class Log:
    def __init__(self):self.events=[]
    def check_cancel(self):pass
    def progress(self,event):self.events.append(event)
    def __call__(self,line):pass


@pytest.fixture
def data(tmp_path):
    net=Network(tmp_path/'data.sqlite')
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('test','/scratch','{}')").lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name) VALUES(?,'test')",(pid,)).lastrowid
    return net,pid,fid


def test_shared_unicode_inputs_reopen_update_null_and_rollback(data):
    net,pid,fid=data
    text='int café(void) { return 42; }\n'*1000
    with net.store.tx() as db:
        db.execute('UPDATE functions SET asm=?,seed=?,best_source=?,matched_source=?,target=? WHERE id=?',
                   (text,text,text,text,b'\x00\xff',fid))
    assert net.store.one('SELECT COUNT(*) FROM text_payloads')[0]==1
    assert net.store.one('SELECT length(asm),typeof(asm),target FROM main.functions WHERE id=?',fid)[:]==(32,'blob',b'\x00\xff')
    reopened=Store(net.store.path)
    row=reopened.one('SELECT functions.asm,seed,best_source,matched_source FROM functions WHERE functions.id=?',fid)
    assert tuple(row)==(text,)*4
    with pytest.raises(RuntimeError):
        with net.store.tx() as db:
            db.execute('UPDATE functions SET seed=? WHERE id=?',('rollback'*1000,fid))
            raise RuntimeError()
    assert net.store.one('SELECT seed FROM functions WHERE id=?',fid)[0]==text
    for value in ('',None,'small',text):
        with net.store.tx() as db:db.execute('UPDATE functions SET seed=? WHERE id=?',(value,fid))
        assert net.store.one('SELECT seed FROM functions WHERE id=?',fid)[0]==value
    assert net.store.one('SELECT matched_source FROM functions WHERE id=?',fid)[0]==text


def test_composite_keys_rowid_order_json_and_metadata_are_lazy(data):
    net,pid,fid=data
    value=json.dumps({'source':'C source'*1000,'score':12})
    with net.store.tx() as db:
        db.executemany('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',[(fid,'one','miss','reason',value),(fid,'two','miss','reason',value)])
    assert [r[0] for r in net.store.all('SELECT signature FROM reuse_trials ORDER BY rowid DESC')]==['two','one']
    assert net.store.one("SELECT json_extract(r.feedback,'$.score') FROM reuse_trials r WHERE signature='one'")[0]==12
    net.store.conn.create_function('payload_read',2,lambda *_:(_ for _ in ()).throw(AssertionError('inflated')))
    with net.store.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM reuse_trials').fetchone()[0]==2
        assert db.execute('SELECT outcome FROM reuse_trials WHERE function_id=? AND signature=?',(fid,'one')).fetchone()[0]=='miss'
        assert db.execute("SELECT 'FROM functions' literal FROM functions LIMIT 1").fetchone()[0]=='FROM functions'


def test_manifests_deduplicate_and_upserts_replace_current_references(data):
    net,pid,fid=data
    manifest=json.dumps({'functions':{'function_'+str(i):'x'*100 for i in range(1000)}})
    with net.store.tx() as db:
        db.execute("INSERT INTO progress_state(project_id,disk,projected,updated_at,disk_manifest,projected_manifest) VALUES(?,'{}','{}',0,?,?)",(pid,manifest,manifest))
    row=net.store.one('SELECT disk_manifest,projected_manifest FROM main.progress_state')
    assert row[0]==row[1] and len(row[0])==32
    assert net.store.one('SELECT disk_manifest FROM progress_state')[0]==manifest
    assert database_storage.collect(net)==0
    with net.store.tx() as db:
        db.execute("INSERT INTO progress_state(project_id,disk,projected,updated_at,disk_manifest,projected_manifest) VALUES(?,'{}','{}',1,?,?) ON CONFLICT(project_id) DO UPDATE SET disk_manifest=excluded.disk_manifest,projected_manifest=excluded.projected_manifest",(pid,'new'*1000,'new'*1000))
    assert database_storage.collect(net)==1
    assert net.store.one('SELECT projected_manifest FROM progress_state')[0]=='new'*1000


def test_lazy_schema_existing_reader_and_insert_column_counts(data):
    net,pid,fid=data
    reader_ready=threading.Event();created=threading.Event();result=[]
    def reader():
        net.store.one('SELECT COUNT(*) FROM projects')
        reader_ready.set();created.wait(10)
        result.append(net.store.one('SELECT record FROM compiler_trial_evidence')[0])
    thread=threading.Thread(target=reader);thread.start();assert reader_ready.wait(10)
    trial_table(net)
    value=json.dumps({'source':'hello'*1000})
    with net.store.tx() as db:
        db.execute('INSERT INTO compiler_trial_evidence VALUES(?,?,?,?,?,?)',('trial',pid,fid,1,value,0))
    created.set();thread.join(10)
    assert result==[value]
    Transcripts(net.store)
    with net.store.tx() as db:
        identity=db.execute('INSERT INTO helper_messages VALUES(NULL,1,0,?,?)',('message',value)).lastrowid
    assert net.store.one('SELECT text FROM helper_messages WHERE id=?',identity)[0]==value


def test_legacy_conversion_cancellation_and_stable_attempt_ids(data):
    net,pid,fid=data
    value=json.dumps({'source':'legacy'*1000})
    with net.store.tx() as db:
        db.execute('DROP TRIGGER large_reuse_trials_feedback_insert')
        db.execute('INSERT INTO reuse_trials VALUES(?,?,?,?,?)',(fid,'old','miss','',value))
        db.execute('DROP VIEW packed_reuse_trials')
        install(db,('reuse_trials',))
        db.execute('DROP TRIGGER pack_attempts_insert')
        aid=db.execute('INSERT INTO attempts(function_id,source) VALUES(?,?)',(fid,'old code'*1000)).lastrowid
        from decompnet.payload_storage import install as install_attempts
        install_attempts(db)
    class Cancel(Log):
        def check_cancel(self):raise RuntimeError('cancel')
    with pytest.raises(RuntimeError):database_storage.pack(net,Cancel())
    assert net.store.one('SELECT typeof(feedback) FROM main.reuse_trials')[0]=='text'
    assert database_storage.pack(net,Log())['packed']==2
    assert database_storage.pack(net,Log())['packed']==0
    assert net.store.one('SELECT feedback FROM reuse_trials')[0]==value
    assert net.store.one('SELECT source FROM attempts WHERE id=?',aid)[0]=='old code'*1000
    assert net.store.one('SELECT COUNT(*) FROM attempts')[0]==1


def test_missing_shared_content_fails_closed(data):
    net,pid,fid=data
    with net.store.tx() as db:
        db.execute('UPDATE functions SET asm=? WHERE id=?',('assembly'*1000,fid))
        db.execute('DELETE FROM text_payloads')
    with pytest.raises(Exception,match='user-defined function'):
        net.store.one('SELECT asm FROM functions WHERE id=?',fid)


def test_large_payload_cache_is_bounded_by_uncompressed_size():
    import zlib

    from decompnet import payload_storage as codec
    text='compressible'*100000
    key=codec.digest(text);payload=zlib.compress(text.encode())
    assert codec.unpack(key,payload)==text
    with codec._cache_lock:assert (key,payload) not in codec._cache


def test_pack_then_compact_reclaims_space_without_losing_content(data):
    from decompnet.app import header_cleanup
    net,pid,fid=data
    text='exact legacy source\n'*200000
    with net.store.tx() as db:
        db.execute('DROP TRIGGER large_functions_asm_insert')
        db.execute('DROP TRIGGER large_functions_asm_update')
        db.execute('UPDATE functions SET asm=? WHERE id=?',(text,fid))
        db.execute('DROP VIEW packed_functions')
        install(db,('functions',))
    import hashlib

    from decompnet.app.match_library import Library, read_text
    library=Library(net)
    library_text='archived verified C\n'*100000
    key=hashlib.sha256(library_text.encode()).hexdigest()
    with library.connect(write=True) as db:db.execute('INSERT INTO texts VALUES(?,?)',(key,library_text))
    before=database_storage.stats(net)['allocated_bytes']
    assert database_storage.pack(net,Log())['packed']==2
    result=header_cleanup.compact(net,Log())
    after=database_storage.stats(net)['allocated_bytes']
    assert after < before/2 and result['reclaimed_bytes']>0
    assert net.store.one('SELECT asm FROM functions WHERE id=?',fid)[0]==text
    with library.connect() as db:
        raw=db.execute('SELECT text FROM texts WHERE hash=?',(key,)).fetchone()[0]
        assert read_text(key,raw)==library_text


def test_backfill_compression_holds_no_writer_and_preserves_concurrent_edits(data,monkeypatch):
    import zlib
    net,pid,fid=data
    with net.store.tx() as db:
        db.execute('DROP TRIGGER large_functions_asm_update')
        db.execute('UPDATE functions SET asm=? WHERE id=?',('old'*10000,fid))
        db.execute('DROP VIEW packed_functions')
        install(db,('functions',))
    original=zlib.compress
    def compress(value):
        assert not getattr(net.store._local,'in_tx',False)
        # A normal write can commit while the backfill prepares the previous value.
        with net.store.tx() as db:
            db.execute('UPDATE functions SET asm=? WHERE id=?',('new edit',fid))
        return original(value)
    monkeypatch.setattr(zlib,'compress',compress)
    assert database_storage.pack(net,Log())['packed']==0
    assert net.store.one('SELECT asm FROM functions WHERE id=?',fid)[0]=='new edit'
    assert net.store.one('SELECT COUNT(*) FROM text_payloads')[0]==0


def test_detail_views_follow_later_schema_migrations(data):
    net,pid,fid=data
    with net.store.tx() as db:
        db.execute('UPDATE functions SET asm=? WHERE id=?',('assembly'*1000,fid))
        db.execute('ALTER TABLE functions ADD COLUMN later_metadata TEXT')
        db.execute('UPDATE functions SET later_metadata=? WHERE id=?',('later',fid))
        install(db,('functions',))
    row=net.store.one('SELECT asm,later_metadata FROM functions WHERE id=?',fid)
    assert tuple(row)==('assembly'*1000,'later')
