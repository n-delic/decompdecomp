"""Large inspector histories stay bounded without dropping stored evidence."""
import json

import pytest

from decompnet.app.function_history import CHART_BUCKETS, NOTE_PAGE, history, notes_page
from decompnet.store import Store


@pytest.fixture
def saved(tmp_path):
    store = Store(tmp_path/'history.db')
    with store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,root,config) VALUES('test','/none','{}')").lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name) VALUES(?,'f')", (pid,)).lastrowid
        aid = db.execute("INSERT INTO agents(name,token_hash) VALUES('you','test')").lastrowid
    yield store, {'id':fid, 'project_id':pid}, aid
    store.conn.close()


def test_sample_preserves_ends_extrema_failures_and_total(saved):
    store, fn, aid = saved
    with store.tx() as db:
        for i in range(10000):
            score = None if i%57 == 0 else 0 if i == 8753 else 12345 if i == 2311 else 500
            db.execute('INSERT INTO attempts(function_id,agent_id,score,created_at) VALUES(?,?,?,?)',
                       (fn['id'], aid, score, i))
    result = history(store, fn)
    points = result['scores']
    assert len(points) <= CHART_BUCKETS*5
    assert result['scores_sampled'] and result['scores_total'] == 10000
    assert (points[0]['t'],points[-1]['t']) == (0,9999)
    assert {p['score'] for p in points} >= {None, 0, 12345}
    assert [p['id'] for p in points] == sorted({p['id'] for p in points})
    assert store.one('SELECT COUNT(*) n FROM attempts')['n'] == 10000


def test_small_history_returns_every_attempt(saved):
    store, fn, aid = saved
    assert history(store, fn)['scores_total'] == 0
    with store.tx() as db:
        db.executemany('INSERT INTO attempts(function_id,agent_id,score,created_at) VALUES(?,?,?,?)',
                       [(fn['id'],aid,n,n) for n in range(12)])
    result = history(store, fn)
    assert [p['score'] for p in result['scores']] == list(range(12))
    assert not result['scores_sampled']


def test_notes_seek_handles_equal_timestamps_and_new_notes_between_pages(saved):
    store, fn, aid = saved
    with store.tx() as db:
        db.executemany('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                       [(fn['project_id'],fn['id'],aid,str(n),100) for n in range(NOTE_PAGE*3+7)])
    page = notes_page(store, fn)
    all_ids = [n['id'] for n in page['notes']]
    assert len(all_ids) == NOTE_PAGE
    with store.tx() as db:
        db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)',
                   (fn['project_id'],fn['id'],aid,'new',101))
    while page['notes_next']:
        page = notes_page(store, fn, page['notes_next'])
        assert len(page['notes']) <= NOTE_PAGE
        all_ids.extend(n['id'] for n in page['notes'])
    assert len(all_ids) == len(set(all_ids)) == NOTE_PAGE*3+7
    assert store.one('SELECT COUNT(*) n FROM notes')['n'] == len(all_ids)+1
    # Cursor carries time+id, so neighbouring functions cannot leak into the page.
    other = dict(fn, id=fn['id']+1)
    assert notes_page(store, other, json.dumps([100,999999]))['notes'] == []


def test_legacy_undated_notes_and_invalid_cursors(saved):
    store, fn, aid = saved
    with store.tx() as db:
        db.executemany('INSERT INTO notes(project_id,function_id,agent_id,text) VALUES(?,?,?,?)',
                       [(fn['project_id'],fn['id'],aid,str(n)) for n in range(NOTE_PAGE+1)])
    first=notes_page(store,fn)
    last=notes_page(store,fn,first['notes_next'])
    assert len(first['notes'])==NOTE_PAGE and len(last['notes'])==1 and last['notes_next'] is None
    for invalid in ('null','[]','[{},2]','not json'):
        with pytest.raises(ValueError,match='Invalid notes cursor'):
            notes_page(store,fn,invalid)
