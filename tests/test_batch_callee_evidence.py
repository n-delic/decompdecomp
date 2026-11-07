"""Shared scheduling evidence is bounded, project-local and never a judge verdict."""
import pytest

from decompnet import candidate_scope, payload_storage
from decompnet.app.agents import SCHEMA
from decompnet.app.engine import RomNetwork
from tests.test_model_pauses import setup
from tests.test_project_database_migration import close


@pytest.fixture(params=[False,True],ids=['shared-file','project-file'])
def data(tmp_path,request):
    net,_=setup(tmp_path,12)
    with net.store.tx() as db:
        db.execute('DROP TABLE helper_sessions')
        db.execute('DROP TABLE helpers')
        for sql in SCHEMA:db.execute(sql)
        db.executemany('INSERT INTO helpers(project,name,kind,model,label,state) VALUES(?,?,?,?,?,?)',
                       [('game',name,'codex',model,name,'stopped') for name,model in [('a','alpha#low'),('b','beta'),('c','alpha#low')]])
        db.execute("UPDATE functions SET best_source='int caller(){ helper_a(); missing(); }'")
        for name in ('helper_a','helper_b'):
            db.execute("INSERT INTO functions(project_id,name,status,matched_source,unit) VALUES(1,?,'matched',?,'a')",
                       (name,('void '+name+'(){}\n')*100))
    if request.param:
        close(net);net=RomNetwork(tmp_path/'app.db',tmp_path,project_databases=True)
    yield net,net.project_store('game'),net._project('game')
    close(net)


def test_batch_reads_each_referenced_helper_once_and_matches_standalone(data,monkeypatch):
    net,store,proj=data
    rows=store.all("SELECT * FROM functions WHERE status='open' ORDER BY id")
    expected=[candidate_scope.state(net,proj,row) for row in rows]
    queries=[];decoded=[];original=store.all
    def select_all(sql,*args):
        if sql.startswith('SELECT name,matched_source'):queries.append(1)
        return original(sql,*args)
    def unpack(key,blob):
        value=payload_storage.unpack(key,blob);decoded.append(value);return value
    monkeypatch.setattr(store,'all',select_all)
    store._reader().create_function('payload_read',2,unpack)
    batch=candidate_scope.batch_context(net,proj)
    actual=[candidate_scope.state(net,proj,row,batch) for row in rows]
    assert actual==expected and len(queries)==1 and len(decoded)==1
    assert batch['callee_evidence']['missing']==[]
    assert all(len(digest)==64 for values in batch['callee_evidence'].values() for _,digest in values)


def test_new_batches_observe_helper_source_status_and_newly_matched_names(data):
    net,store,proj=data;fn=net._function('f0','game')
    def evidence():return candidate_scope.state(net,proj,fn,candidate_scope.batch_context(net,proj))[-1]
    old=evidence()
    with store.tx() as db:db.execute("UPDATE functions SET matched_source='new helper source' WHERE name='helper_a'")
    changed=evidence();assert changed!=old
    with store.tx() as db:db.execute("UPDATE functions SET status='open' WHERE name='helper_a'")
    assert evidence()==[]
    with store.tx() as db:db.execute("INSERT INTO functions(project_id,name,status,matched_source,unit) VALUES(1,'missing','matched','void missing(){}','a')")
    assert evidence()==[('missing',candidate_scope.hashlib.sha256(b'void missing(){}').hexdigest())]


def test_source_parser_order_cap_and_bounded_fallback_keep_identical_signatures(data,monkeypatch):
    net,store,proj=data;fn=dict(net._function('f0','game'))
    monkeypatch.setattr(candidate_scope,'BATCH_CALLEES',1)
    batch=candidate_scope.batch_context(net,proj)
    for source in ('int f0(){ helper_a(); }', 'int f0(){ helper_b(); helper_a(); }',
                   'int f0(){ /* helper_b(); */ helper_a(); puts("helper_b()"); }',
                   'int f0(){'+''.join(f' aaa{i}();' for i in range(25))+' helper_b(); }'):
        current={**fn,'best_source':source}
        assert candidate_scope.state(net,proj,current,batch)==candidate_scope.state(net,proj,current)
        assert len(batch['callee_evidence'])<=1


def test_selected_payload_still_checks_checksum_in_a_new_batch(data):
    net,store,proj=data;fn=net._function('f0','game')
    candidate_scope.state(net,proj,fn,candidate_scope.batch_context(net,proj))
    with store.tx() as db:
        db.execute('DELETE FROM text_payloads WHERE hash=?',(payload_storage.digest(('void helper_a(){}\n')*100),))
    with pytest.raises(Exception,match='user-defined function'):
        candidate_scope.state(net,proj,fn,candidate_scope.batch_context(net,proj))
