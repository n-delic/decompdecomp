"""Room credit is project-scoped, distinct per person, and retains earlier contributions."""
from decompnet.app.collab import Room
from tests.test_claims import _game


def test_distinct_member_improvements_include_legacy_and_matches(tmp_path):
    net, _ = _game(tmp_path, [('first', 40, 'open'), ('second', 40, 'open'), ('third', 40, 'open')])
    room = Room(net, 'game')
    members = {}
    for owner in ('sam', 'lee', 'idle'):
        member, _ = net.local_agent(owner)
        with net.store.tx() as db:
            db.execute('UPDATE agents SET origin=?,owner=NULL WHERE id=?', (room.origin, member.id))
        members[owner] = []
        for name in ('one', 'two'):
            agent, _ = net.local_agent(owner+'.'+name)
            with net.store.tx() as db:
                db.execute('UPDATE agents SET origin=?,owner=? WHERE id=?', (room.origin, owner, agent.id))
            members[owner].append(agent.id)
    functions = {r['name']: r['id'] for r in net.store.all('SELECT id,name FROM functions')}
    def attempt(function, owner, worker, score, verdict, compiled=1):
        with net.store.tx() as db:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,source,score_improved) VALUES(?,?,?,?,?,?)',
                       (functions[function], members[owner][worker], score, compiled, '', verdict))
    attempt('first', 'sam', 0, 100, 1)
    attempt('first', 'sam', 1, 90, 1)  # same person's second worker, still one function
    attempt('first', 'lee', 0, 0, 1)  # both contributors retain improvement credit
    attempt('second', 'lee', 0, 80, None)  # older/fork history
    attempt('second', 'sam', 0, 85, None)  # worse than another member, no credit
    attempt('second', 'sam', 0, 70, None)
    attempt('third', 'sam', 0, 1, 0)  # rejected/stale verdict must override historical inference
    attempt('third', 'idle', 0, 0, 1, compiled=0)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_agent_id=? WHERE id=?",
                   (members['lee'][0], functions['first']))
        other = db.execute("INSERT INTO projects(slug,root,config) VALUES('other','/tmp','{}')").lastrowid
        foreign = db.execute("INSERT INTO functions(project_id,name,n_insns,status,matched_agent_id) VALUES(?, 'elsewhere',40,'matched',?)",
                             (other, members['sam'][0])).lastrowid
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,score_improved) VALUES(?,?,0,1,1)',
                   (foreign, members['sam'][0]))
    board = {m['name']: m for m in room._members_now()}
    assert (board['sam']['improved'], board['sam']['matched']) == (2, 0)
    assert (board['lee']['improved'], board['lee']['matched']) == (2, 1)
    assert (board['idle']['improved'], board['idle']['matched']) == (0, 0)
    assert room.members() == list(board.values())
