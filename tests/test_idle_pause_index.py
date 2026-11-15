"""Idle receipts must not cross-scan unrelated settings for each project function."""
import json

from decompnet.app import work_availability
from tests.test_claims import _game

OLD_QUERY=("SELECT s.key,s.value FROM settings s JOIN functions f ON "
           "s.key=? || f.id OR s.key GLOB ? || f.id || ':*' WHERE f.project_id=? ORDER BY s.key")


def test_indexed_pause_records_preserve_legacy_scopes_prefixes_and_project_isolation(tmp_path):
    net,pid=_game(tmp_path,[('one',4,'open'),('two',4,'matched')])
    with net.store.tx() as db:
        other=db.execute("INSERT INTO projects(slug,name,root,config) VALUES('other','Other','','{}')").lastrowid
        db.execute("INSERT INTO functions(id,project_id,name,status,unit) VALUES(10,?,'ten','open','')",(other,))
        records=[('agent-stall:1','legacy'),('agent-stall:1:','empty scope'),
                 ('agent-stall:1:model:abc','model'),('agent-stall:1:worker:def','worker'),
                 ('agent-stall:1:模型','unicode'),('agent-stall:2:any','matched still contributes'),
                 ('agent-stall:10:model','other project'),('agent-stall:1junk','not a scope'),
                 ('agent-stall:11:model','missing function'),('unrelated','ignore')]
        db.executemany('INSERT INTO settings VALUES(?,?)',records)
    expected=[tuple(r) for r in net.store.all(OLD_QUERY,'agent-stall:','agent-stall:',pid)]
    actual=[tuple(r) for r in work_availability.pause_records(net,pid)]
    assert actual==expected
    assert {r[0] for r in actual}=={key for key,_ in records[:6]}


def test_pause_record_work_does_not_multiply_function_and_unrelated_setting_counts(tmp_path):
    net,pid=_game(tmp_path,[(f'f{i}',4,'open') for i in range(120)])
    with net.store.tx() as db:
        db.executemany('INSERT INTO settings VALUES(?,?)',[(f'unrelated:{i}',str(i)) for i in range(5000)])
        db.executemany('INSERT INTO settings VALUES(?,?)',[(f'agent-stall:{i}:model',str(i)) for i in range(1,121)])
    reader=net.store._reader()
    steps=[]
    reader.set_progress_handler(lambda:steps.append(1) or 0,100)
    try:
        rows=work_availability.pause_records(net,pid)
    finally:
        reader.set_progress_handler(None,0)
    assert len(rows)==120
    # VM instruction counts are independent of machine speed. A cross-product
    # scan executes millions; indexed point/range seeks stay well below this.
    assert len(steps)*100<100000


def test_idle_version_still_invalidates_for_scoped_and_legacy_pause_edits(tmp_path):
    net,pid=_game(tmp_path,[('one',4,'open')]);agent,_=net.local_agent('a')
    base=work_availability.version(net,agent,'game')
    for key in ('agent-stall:1','agent-stall:1:model-alpha','agent-stall:1:model-beta'):
        with net.store.tx() as db:db.execute('INSERT INTO settings VALUES(?,?)',(key,json.dumps({'count':3})))
        changed=work_availability.version(net,agent,'game')
        assert changed!=base
        base=changed
        with net.store.tx() as db:db.execute('UPDATE settings SET value=? WHERE key=?',('{"count":0}',key))
        changed=work_availability.version(net,agent,'game')
        assert changed!=base
        base=changed
    with net.store.tx() as db:db.execute("INSERT INTO settings VALUES('agent-stall:10:model-other','{}')")
    assert work_availability.version(net,agent,'game')==base


def test_idle_version_retains_same_digest_as_original_query(tmp_path,monkeypatch):
    net,pid=_game(tmp_path,[('one',4,'open')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:
        db.executemany('INSERT INTO settings VALUES(?,?)',[('agent-stall:1','{}'),('agent-stall:1:model','{}')])
    indexed=work_availability.version(net,agent,'game')
    monkeypatch.setattr(work_availability,'pause_records',lambda net,pid:net.store.all(OLD_QUERY,'agent-stall:','agent-stall:',pid))
    assert work_availability.version(net,agent,'game')==indexed
