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


def test_batch_resolves_owner_once_per_function_even_with_warm_inputs(data,monkeypatch):
    net,store,proj=data
    rows=store.all("SELECT * FROM functions WHERE status='open' ORDER BY id")
    batch=candidate_scope.batch_context(net,proj)
    expected=[candidate_scope.key(net,proj,row,batch) for row in rows]
    calls=[];resolve=net.project_store
    def owner(project):
        calls.append(project)
        return resolve(project)
    monkeypatch.setattr(net,'project_store',owner)
    assert [candidate_scope.key(net,proj,row,batch) for row in rows]==expected
    assert calls==[proj['id']]*len(rows)


def test_warm_batch_still_checks_catalog_and_rejects_replaced_store(data,monkeypatch):
    from decompnet.network import NetworkError
    net,store,proj=data;fn=net._function('f0','game')
    batch=candidate_scope.batch_context(net,proj)
    candidate_scope.key(net,proj,fn,batch)
    def removed(project):raise NetworkError('Project was removed')
    with monkeypatch.context() as patch:
        patch.setattr(net,'project_store',removed)
        with pytest.raises(NetworkError,match='removed'):candidate_scope.key(net,proj,fn,batch)
        with pytest.raises(NetworkError,match='removed'):candidate_scope.context(net,proj,fn['unit'],batch)
        patch.setattr(net,'project_store',lambda _:object())
        with pytest.raises(NetworkError,match='another project or database'):candidate_scope.key(net,proj,fn,batch)


def joined_scan_state(net,proj,fn):
    """The scope as it was read before its pieces were cached: one parse of the settings and one scan of the joined
    fields for every call, the helpers read fresh. Every cached call must give exactly this."""
    import json
    real_settings,real_scan=candidate_scope.settings,candidate_scope._bounded
    try:
        candidate_scope.settings=lambda text:json.loads(text)
        candidate_scope._bounded=lambda cache,key,build,limit:build()
        return candidate_scope.state(net,proj,fn,candidate_scope.batch_context(net,proj))
    finally:
        candidate_scope.settings,candidate_scope._bounded=real_settings,real_scan


def test_candidates_reuse_settings_scans_and_helpers_and_give_the_same_scope(data,monkeypatch):
    # A deep search asks for one function's scope with every candidate's source (deep_profiles.probe): the settings,
    # the function's own scan and each helper's evidence are read once, and every scope is what a fresh read gives.
    from decompnet import candidate_work, project
    net,store,proj=data;fn=dict(net._function('f0','game'))
    proj=store.held_one('SELECT * FROM projects WHERE id=?',proj['id'])
    sources=['int f0(){ helper_a(); }','int f0(){ helper_a(); helper_b(); }','int f0(){ return D_80001234 + 0x10; }',
             'int f0(){ helper_b(); }','int f0(){ helper_a(); /* again */ }']
    expected=[joined_scan_state(net,proj,{**fn,'best_source':source}) for source in sources]
    parses=[];loads=project.json.loads
    monkeypatch.setattr(project.json,'loads',lambda text,*a,**k:parses.append(1) or loads(text,*a,**k))
    project._settings.clear()
    queries=[];original=store.all
    def select_all(sql,*args):
        if sql.startswith('SELECT name,matched_source'):queries.append(args[1])
        return original(sql,*args)
    monkeypatch.setattr(store,'all',select_all)
    with candidate_work.scope():
        actual=[candidate_scope.state(net,proj,{**fn,'best_source':source}) for source in sources]
        keys=[candidate_scope.key(net,proj,{**fn,'best_source':source}) for source in sources]
    assert actual==expected
    assert keys==[candidate_scope.hashlib.sha256(repr(value).encode()).hexdigest() for value in expected]
    assert len(parses)==1
    # (helper_a and helper_b once each, besides the names no matched function has)
    assert sum(name in query for query in queries for name in ('"helper_a"','"helper_b"'))==2


def test_a_cached_scope_sees_a_helper_change_and_new_settings(data):
    from decompnet import candidate_work
    net,store,proj=data;fn={**dict(net._function('f0','game')),'best_source':'int f0(){ helper_a(); }'}
    def scope():
        row=store.held_one('SELECT * FROM projects WHERE id=?',proj['id'])
        with candidate_work.scope():return candidate_scope.state(net,row,fn),candidate_scope.key(net,row,fn)
    before=scope()
    with store.tx() as db:db.execute("UPDATE functions SET matched_source='changed helper' WHERE name='helper_a'")
    helper=scope()
    assert helper[0][-1]!=before[0][-1] and helper[1]!=before[1]
    with store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',
                   (candidate_scope.json.dumps({**net.cfg(proj),'gp':1234}),proj['id']))
    settings=scope()
    assert settings[0][0]['gp']==1234 and settings[1]!=helper[1]


def test_a_scope_never_hands_out_the_shared_settings(data):
    net,store,proj=data;fn=net._function('f0','game')
    with store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',
                   (candidate_scope.json.dumps({'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}),proj['id']))
    proj=store.held_one('SELECT * FROM projects WHERE id=?',proj['id'])
    value=candidate_scope.state(net,proj,fn)
    value[0]['compiler']['flags']='changed'
    assert candidate_scope.state(net,proj,fn)[0]['compiler']['flags']!='changed'
    assert net.cfg(proj)['compiler'].get('flags')!='changed'


def test_rows_a_caller_built_with_unhashable_values_still_key_exactly():
    assert candidate_scope._frozen(({'a':1},{'b':'x'}))==(( ('a',1),),(('b','x'),))
    assert candidate_scope._frozen(({'a':[1]},{}))==candidate_scope._frozen(({'a':[1]},{}))
    assert candidate_scope._frozen(({'a':[1]},{}))!=candidate_scope._frozen(({'a':[2]},{}))
