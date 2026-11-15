"""Project-owned repair review state refers to one shared immutable evidence archive."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from decompnet.network import Agent
from tests import test_project_storage as base

scoped = base.scoped


def capture(net, stores, pid=1):
    slug = 'first' if pid==1 else 'second'
    with stores[pid].tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind':'rom','platform':'n64'}),))
        db.execute("UPDATE agents SET name='stale copied label'")
    fn = net._function('same_name',slug)
    agent = Agent(1,'worker',None)
    net._record(fn,agent,100,'int same_name(void){return 1;}',1)
    net._record(net._function('same_name',slug),agent,0,'int same_name(void){return 0;}',1,matched=True)
    return stores[pid].one('SELECT id FROM repair_examples')['id']


def test_project_review_reads_shared_author_and_archive_on_reader_threads(scoped):
    net, stores = scoped
    cid = capture(net,stores)
    feedback = net.repair_feedback
    detail = feedback.detail('first',cid)
    assert detail['agent'] == 'worker'
    assert detail['evidence']['agent']['name'] == 'worker'
    assert detail['evidence']['after']['source'] == 'int same_name(void){return 0;}'
    assert not net.store.one('SELECT 1 FROM repair_examples')
    assert feedback.listing('second')['examples'] == []
    feedback.review('first',cid,'reviewed','constants','checked')
    assert feedback.detail('',cid)['review_note'] == 'checked'
    exported = json.loads(''.join(feedback.export(include_source=True)))
    assert len(exported['examples']) == 1
    assert exported['examples'][0]['slug'] == 'first'
    with ThreadPoolExecutor(max_workers=1) as pool:
        copied = pool.submit(feedback.detail,'first',cid).result(timeout=5)
    assert copied['evidence']['after']['source'] == detail['evidence']['after']['source']
    shared = Path(net.store.conn.repair_archive.path)
    assert shared.is_file()
    assert stores[1].conn.repair_archive.path == str(shared)
    assert not Path(stores[1].path+'.repair-evidence.sqlite3').exists()


def test_global_listing_exports_and_analysis_visit_owning_files(scoped, tmp_path, monkeypatch):
    import pytest

    from decompnet.app import repair_outcomes
    net, stores = scoped
    first = capture(net,stores,1)
    # Simulate preserved globally allocated repair IDs, as required by migration.
    with stores[2].tx() as db:db.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES('repair_examples',199)")
    second = capture(net,stores,2)
    feedback = net.repair_feedback
    rows = feedback.listing()['examples']
    assert [(r['id'],r['slug']) for r in rows] == [(second,'second'),(first,'first')]
    assert feedback.listing()['total'] == 2
    path = tmp_path/'submitted-repairs.sqlite3'
    feedback.export_database(path)
    assert path.is_file()
    assert [row['slug'] for row in json.loads(''.join(feedback.export()))['examples']] == ['first','second']
    net.forks = object()
    monkeypatch.delenv('DECOMP_REPAIR_ANALYSIS',raising=False)
    queue = []
    monkeypatch.setattr(repair_outcomes.purpose_work,'start',lambda purpose,project,work:queue.append(work))
    monkeypatch.setattr(repair_outcomes,'analyze',lambda row:{'summary':{'project':row['slug']}})
    owner = feedback.for_project('second')
    with pytest.raises(RuntimeError,match='rollback'):
        with stores[2].tx():
            repair_outcomes.schedule(owner,second)
            assert not queue
            raise RuntimeError('rollback')
    assert not queue and repair_outcomes.result(owner,second) is None
    with stores[2].tx():
        repair_outcomes.schedule(owner,second)
        assert not queue
    assert len(queue) == 1
    queue.pop()()
    assert repair_outcomes.result(feedback,second)['report']['summary']['project'] == 'second'
    assert repair_outcomes.backlog(feedback) == {'done':1}
    assert not net.store.one("SELECT 1 FROM sqlite_master WHERE name='repair_outcome_analysis'")


def test_global_repair_pages_follow_time_across_project_identifier_ranges(scoped):
    net,stores = scoped
    first=capture(net,stores,1)
    with stores[2].tx() as db:db.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES('repair_examples',199)")
    second=capture(net,stores,2)
    with stores[1].tx() as db:db.execute('UPDATE repair_examples SET created_at=200 WHERE id=?',(first,))
    with stores[2].tx() as db:db.execute('UPDATE repair_examples SET created_at=100 WHERE id=?',(second,))
    assert first<second
    assert [row['id'] for row in net.repair_feedback.listing()['examples']]==[first,second]
    assert [row['id'] for row in net.repair_feedback.listing(before=first)['examples']]==[second]
