"""Large project payloads are loaded only where required; caches follow committed database edits."""
import copy
import json
from types import SimpleNamespace

import pytest

from decompnet.app import agents, boundary_repairs, code_coverage, session_history
from decompnet.network import NetworkError
from tests.test_code_coverage import fixture as coverage_fixture
from tests.test_compiler_default_test import net as compiler_fixture

compiler_net=pytest.fixture(compiler_fixture.__wrapped__)


@pytest.mark.parametrize('race',[None,'target','best_source','rom_offset'])
def test_range_change_loads_only_affected_bodies_and_keeps_race_guard(compiler_net,monkeypatch,race):
    net=compiler_net
    proj=net._project('game')
    cfg=copy.deepcopy(net.cfg(proj));cfg['compiler_ranges'][0]['flags']=['-O0']
    queries=[]
    original=net.store.all
    def read(sql,*args):
        rows=original(sql,*args)
        if 'SELECT * FROM functions' in sql:
            queries.append(sql)
            assert {r['name'] for r in rows}=={'f2','f3'}
        return rows
    monkeypatch.setattr(net.store,'all',read)
    def regression(trial,header,**kw):
        assert [r['name'] for r in kw['only']]==['f2']
        return []
    monkeypatch.setattr(net,'regression_test',regression)
    def judge(*args):
        if race:
            value={'target':b'changed','best_source':'new agent source','rom_offset':123}[race]
            with net.store.tx() as db:db.execute(f"UPDATE functions SET {race}=? WHERE name='f3'",(value,))
        return False,SimpleNamespace(score=5),''
    monkeypatch.setattr(net,'_judge_with',judge)
    if race:
        with pytest.raises(NetworkError,match='changed during'):net._apply_compiler_config(proj,cfg)
        assert net._project('game')['config']==proj['config']
    else:
        net._apply_compiler_config(proj,cfg)
        assert net._function('f3','game')['best_score']==5
        assert net._function('f0','game')['best_score']==0
    assert queries and all('json_each' in sql for sql in queries)


def test_boundary_cache_ignores_other_projects_and_room_cursor_changes(tmp_path,monkeypatch):
    net,proj=coverage_fixture(tmp_path)
    count=[];original=code_coverage._boundaries
    monkeypatch.setattr(code_coverage,'_boundaries',lambda *a:count.append(1) or original(*a))
    assert code_coverage.boundaries(net,proj)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,root,config) VALUES('other','Other','/unused','{}')").lastrowid
        db.execute("INSERT INTO functions(project_id,name,target,meta) VALUES(?,'other',?,?)",(pid,b'other','{}'))
        cfg=json.loads(proj['config']);cfg['link']={'cursor':{'e':123}}
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    code_coverage.boundaries(net,net.project_row('game'))
    assert len(count)==1
    with net.store.tx() as db:db.execute("UPDATE functions SET meta=? WHERE project_id=?",('{"boundary_override":true}',proj['id']))
    assert code_coverage.boundaries(net,net.project_row('game'))[0]['override']
    assert len(count)==2


def test_session_history_and_efficiency_cache_invalidate_for_edits_context_and_cleanup(compiler_net,monkeypatch):
    net=compiler_net
    with net.store.tx() as db:
        for statement in agents.SCHEMA:db.execute(statement)
        db.execute("ALTER TABLE helper_sessions ADD COLUMN usage_coverage TEXT NOT NULL DEFAULT 'unknown'")
        session_history.prepare(db)
        sid=db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at,matches) VALUES(7,'game','api','model','agent',1,2,1)").lastrowid
        db.execute('INSERT INTO settings VALUES(?,?)',('session-efficiency:'+str(sid),'{"cohort":"original"}'))
    reads=[];original=net.store.all
    def read(sql,*args):
        if 'FROM helper_sessions' in sql:reads.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(net.store,'all',read)
    v,rows=session_history.records(net,7,'agent')
    first=session_history.efficiency(net,7,'agent',v,0,rows)
    assert session_history.records(net,7,'agent')[1] is rows and len(reads)==1
    assert session_history.efficiency(net,7,'agent',v,0,rows) is first
    with net.store.tx() as db:db.execute('UPDATE helper_sessions SET compiles=3 WHERE id=?',(sid,))
    v,rows=session_history.records(net,7,'agent')
    assert rows[0]['compiles']==3 and len(reads)==2
    assert session_history.efficiency(net,7,'agent',v,0,rows)[0]['compiles_per_match']==3
    with net.store.tx() as db:db.execute('UPDATE settings SET value=? WHERE key=?',('{"cohort":"updated"}','session-efficiency:'+str(sid)))
    assert session_history.efficiency(net,7,'agent',v,0,rows)[0]['cohort']=='updated'
    with net.store.tx() as db:db.execute('DELETE FROM helper_sessions WHERE id=?',(sid,))
    assert session_history.records(net,7,'agent')[1]==[]


def test_blocked_function_ids_reuse_the_same_project_revision(tmp_path,monkeypatch):
    net,proj=coverage_fixture(tmp_path)
    calls=[];original=net.store.one
    def read(sql,*args):
        if 'SELECT id,name,gone_mark FROM functions' in sql:calls.append(sql)
        return original(sql,*args)
    monkeypatch.setattr(net.store,'one',read)
    assert boundary_repairs.blocked(net,proj)
    assert boundary_repairs.blocked(net,proj) and len(calls)==1
    with net.store.tx() as db:db.execute("UPDATE functions SET meta=?",('{"boundary_override":true}',))
    assert not boundary_repairs.blocked(net,proj) and len(calls)==2
