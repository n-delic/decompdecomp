"""A matched function's later exact source replaces the saved one; a source that isn't exact never does.

The previous matching source stays among the function's attempts, and polishing keeps working on top of it."""
import pytest

from decompnet.app.engine import RomNetwork as Network
from decompnet.compiler import CompileResult
from decompnet.elfobj import FunctionObj


@pytest.fixture
def judged(tmp_path, monkeypatch):
    net = Network(tmp_path / 'test.db', tmp_path)
    target = FunctionObj('f', 'mips', True, bytes.fromhex('03e0000800000000'), [])
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(1,'game','Game','mips','.', '{\"kind\":\"rom\"}')")
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) VALUES(1,1,'','initial',0,'')")
        db.execute("INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,unit) VALUES(1,1,'f',8,2,?,'[]','')", (target.code,))
    agent = net.authenticate(net.register_agent('alice', 'test'))
    net._attempt_compilation = lambda *a: CompileResult(True, b'object', '')
    verdict = [True]
    net._evaluate = lambda *a: (target, verdict[0], [])
    net._target_obj = lambda *a: target
    net._candidate_checks = lambda *a: {'linked_bytes': {'exact': verdict[0]}}
    monkeypatch.setattr('decompnet.source_lines.mismatches', lambda *a, **kw: {'available': False})
    return net, agent, verdict


def row(net):
    return net.store.one('SELECT * FROM functions WHERE id=1')


FIRST = 'void f(void) { int x = 0; (void)x; }'
CLEAN = 'void f(void) {}'


def test_a_second_exact_source_replaces_the_first_and_the_first_stays_in_history(judged):
    net, agent, _ = judged
    assert net.attempt(agent, 'f', FIRST, 'game')['new_match']
    before = row(net)
    result = net.attempt(agent, 'f', CLEAN, 'game')
    assert result['matched'] and result['exact'] and result['replaced'] and not result['new_match']
    now = row(net)
    assert now['status'] == 'matched' and now['matched_source'] == CLEAN and now['best_source'] == CLEAN
    assert now['matched_at'] == before['matched_at'] and now['matched_agent_id'] == before['matched_agent_id']
    kept = [r['source'] for r in net.store.all('SELECT source FROM attempts WHERE function_id=1 AND score=0')]
    assert FIRST in kept and CLEAN in kept
    assert net.store.one("SELECT 1 FROM events WHERE kind='rematch' AND detail='f'")


def test_an_older_match_without_an_attempt_is_still_kept(judged):
    net, agent, _ = judged
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source=?,best_score=0 WHERE id=1", (FIRST,))
    assert net.attempt(agent, 'f', CLEAN, 'game')['replaced']
    assert FIRST in [r['source'] for r in net.store.all('SELECT source FROM attempts WHERE function_id=1')]


def test_the_same_source_is_only_a_recheck(judged):
    net, agent, _ = judged
    net.attempt(agent, 'f', CLEAN, 'game')
    again = net.attempt(agent, 'f', CLEAN + '\n', 'game')
    assert not again['matched'] and 'EXACT recheck' in again['text'] and 'replaced' not in again
    assert row(net)['matched_source'] == CLEAN


def test_a_source_that_is_not_exact_never_replaces_a_match(judged):
    net, agent, verdict = judged
    net.attempt(agent, 'f', FIRST, 'game')
    verdict[0] = False
    failed = net.attempt(agent, 'f', CLEAN, 'game')
    assert not failed['matched'] and not failed['exact'] and not failed['adopted']
    now = row(net)
    assert now['status'] == 'matched' and now['matched_source'] == FIRST and now['best_source'] == FIRST


def test_a_replacement_is_unpolished_until_polishing_marks_it_readable(judged):
    net, agent, _ = judged
    net.attempt(agent, 'f', FIRST, 'game')
    proj = net.project_row('game', sync=False)
    polished = '/* Does nothing. */ void f(void) {}'
    net.readability._readable(proj, row(net), agent, polished)
    assert row(net)['readable'] == 1 and row(net)['unpolished_source'] == FIRST
    assert net.attempt(agent, 'f', CLEAN, 'game')['replaced']
    now = row(net)
    assert now['readable'] == 0 and now['unpolished_source'] is None and now['matched_source'] == CLEAN
    net.readability._readable(proj, now, agent, polished)
    after = row(net)
    assert after['readable'] == 1 and after['matched_source'] == polished and after['unpolished_source'] == CLEAN


def test_a_fork_keeps_the_previous_source_and_unpolishes(judged):
    """Fork mode writes the file itself (ForkEngine.attempt); its bookkeeping is the same as ROM's."""
    from decompnet.app.forkengine import ForkEngine
    net, agent, _ = judged
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source=?,readable=1,unpolished_source='x' WHERE id=1", (FIRST,))
    ForkEngine._kept_as_history(type('E', (), {'net': net})(), row(net), agent, FIRST, polishing=True)
    assert row(net)['readable'] == 1
    ForkEngine._kept_as_history(type('E', (), {'net': net})(), row(net), agent, FIRST, polishing=False)
    assert row(net)['readable'] == 0
    assert FIRST in [r['source'] for r in net.store.all('SELECT source FROM attempts WHERE function_id=1')]
