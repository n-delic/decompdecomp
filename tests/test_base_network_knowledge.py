"""Full briefs also serve library/room callers without the GUI engine."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet import candidate_quality
from decompnet.app.knowledge import service
from decompnet.network import Network


@pytest.mark.parametrize('split', [False, True])
def test_base_network_brief_keeps_scoped_knowledge_and_reuses_service(tmp_path, split):
    net = Network(tmp_path / 'app.db')
    with net.store.tx() as db:
        for pid in (1, 2):
            db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(?,?,?,?,?)',
                       (pid, f'game{pid}', 'Synthetic', str(tmp_path), json.dumps({'kind': 'rom'})))
    if split:
        net.store.conn.close()
        net = Network(tmp_path / 'app.db', project_databases=True)
    store = net.project_store(1)
    with store.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,status) VALUES(1,1,'target','open')")
    assert not hasattr(net, 'knowledge')
    fn = store.one('SELECT * FROM functions WHERE id=1')
    # Previously raised AttributeError before reaching the actual claim's brief.
    assert candidate_quality.brief(net, fn) == []
    knowledge = net.knowledge
    with knowledge._store(1).tx() as db:
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(1,'target uses a counter','accepted',0)")
    with knowledge._store(2).tx() as db:
        db.execute("INSERT INTO knowledge(project_id,text,status,created_at) VALUES(2,'target private to other project','accepted',0)")
    text = '\n'.join(candidate_quality.brief(net, fn))
    assert 'target uses a counter' in text
    assert 'private to other project' not in text
    assert net.knowledge is knowledge
    assert store.one('SELECT status FROM functions WHERE id=1')[0] == 'open'
    assert store.one('SELECT COUNT(*) FROM attempts')[0] == 0


def test_concurrent_first_use_creates_one_knowledge_service(tmp_path):
    net = Network(tmp_path / 'app.db')
    with ThreadPoolExecutor(max_workers=4) as pool:
        services = list(pool.map(lambda _: service(net), range(12)))
    assert all(item is services[0] for item in services)
