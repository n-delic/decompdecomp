from decompnet.app.agents import Manager
from decompnet.app.engine import RomNetwork


def test_numeric_helper_ids_survive_external_text_session_history(tmp_path):
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    manager=Manager(net,tmp_path,'http://127.0.0.1:9/mcp')
    with net.store.tx() as db:
        for helper in ('external-agent','80'):
            db.execute("INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at) VALUES(?,'game','api','test','worker',0)",(helper,))
    session=manager._record('game','api','next','Next',{})
    assert session.id=='81'
    assert net.store.one('SELECT COUNT(*) AS n FROM helper_sessions')['n']==2
