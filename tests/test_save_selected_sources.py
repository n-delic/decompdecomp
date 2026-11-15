"""Progress renders decode the exported C once, preserving source selection and integrity."""
import sqlite3
import zlib
from types import SimpleNamespace

import pytest

from decompnet import payload_storage
from decompnet.app import saves
from decompnet.store import Store


@pytest.fixture
def saved_sources(tmp_path):
    store = Store(tmp_path/'db')
    def large(label):return '/* '+label+' */\n'+('synthetic source\n'*100)
    values = [('matched','matched',large('match'),large('unused best'),4),
              ('open','open',large('unused match'),large('best'),3),
              ('empty_match','matched','',large('fallback'),2),
              ('null_match','matched',None,large('null fallback'),1),
              ('unscored','open',None,large('unscored best'),None),
              ('empty','open','','',0), ('missing','open',None,None,0),
              ('inline','matched','short match','short best',1),
              ('inline_best','open',None,'short best',1),
              ('unscored_match','matched',large('match without score'),large('unused without score'),None),
              ('aside','asm',None,large('aside best'),1)]
    with store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,root,config) VALUES(1,'game','','{}'),(2,'other','','{}')")
        for address,(name,status,matched,best,score) in enumerate(values):
            db.execute('INSERT INTO functions(project_id,name,vram,status,matched_source,best_source,best_score) '
                       'VALUES(1,?,?,?,?,?,?)',(name,address,status,matched,best,score))
        db.execute("INSERT INTO functions(project_id,name,status,matched_source) VALUES(2,'other','matched',?)",
                   (large('other project'),))
    yield store,SimpleNamespace(project_store=lambda _:store)
    store.conn.close()


def test_render_reads_only_selected_source_once(saved_sources, monkeypatch):
    store,net = saved_sources
    original = [dict(r) for r in store.all(
        "SELECT name,unit,vram,size,status,matched_source,matched_agent_id,best_source,best_score,best_agent_id,readable,meta,"
        "CASE WHEN matched_source IS NOT NULL AND matched_source != '' THEN 1 ELSE 0 END AS has_matched,"
        "CASE WHEN best_source IS NOT NULL AND best_source != '' THEN 1 ELSE 0 END AS has_best "
        "FROM functions WHERE project_id=1 ORDER BY vram")]
    expected = saves._source_files(original)
    reads = []
    unpack = payload_storage.unpack
    def read(digest,blob):
        reads.append(digest)
        return unpack(digest,blob)
    monkeypatch.setattr(payload_storage, 'unpack', read)
    for conn in (store.conn,store._reader()):
        conn.create_function('payload_read',2,read,deterministic=True)
    rows = saves._function_rows(net,1,sources=True)
    assert saves._source_files(rows) == expected
    selected = [r['matched_source'] if r['status']=='matched' and r['has_matched'] else r['best_source']
                for r in original if r['status']=='matched' and r['has_matched'] or r['has_best'] and r['best_score'] is not None]
    assert sorted(reads) == sorted(payload_storage.digest(s) for s in selected if len(s.encode())>=512)
    metadata = [{k:v for k,v in r.items() if k not in {'matched_source','best_source'}} for r in original]
    assert [{k:v for k,v in dict(r).items() if k not in {'matched_source','best_source'}} for r in rows] == metadata
    reads.clear()
    assert [dict(r) for r in saves._function_rows(net,1,sources=False)] == metadata
    assert reads == []
    # Partial saves use the same selection, including an empty requested set and project isolation.
    for names in (['matched','null_match'],['inline_best','other'],[]):
        reads.clear()
        partial = saves._source_files(saves._function_rows(net,1,sources=True,names=names))
        assert partial == {path:text for path,text in expected.items() if path.split('/')[1][:-2] in names}
        assert len(reads) == sum(len(text.encode())>=512 for text in partial.values())


def test_selected_source_still_requires_payload_integrity(saved_sources):
    store,net = saved_sources
    row = store.one("SELECT best_source FROM main.functions WHERE name='matched'")
    with store.tx() as db:
        db.execute('UPDATE text_payloads SET payload=? WHERE hash=?',
                   (zlib.compress(b'corrupted unused source'),row['best_source'].hex()))
    # The unused best attempt cannot prevent exporting the intact matched source.
    assert 'src/matched.c' in saves._source_files(saves._function_rows(net,1,sources=True,names=['matched']))
    with store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE name='matched'")
    with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
        saves._function_rows(net,1,sources=True,names=['matched'])


def test_shared_sources_use_bounded_transport_and_one_decode(saved_sources, monkeypatch):
    store, net = saved_sources
    source = '/* shared synthetic source */\n' * 100
    with store.tx() as db:
        db.executemany("INSERT INTO functions(project_id,name,vram,status,matched_source) VALUES(1,?,?,'matched',?)",
                       [(f'shared{i}',2**60+i,source) for i in range(300)])
    calls, sizes = [], []
    unpack, all_rows = payload_storage.unpack, store.all
    def read(key, blob):
        calls.append(key)
        return unpack(key, blob)
    def counted(sql, *args):
        rows = all_rows(sql, *args)
        sizes.append(len(rows))
        return rows
    monkeypatch.setattr(payload_storage, 'unpack', read)
    monkeypatch.setattr(store, 'all', counted)
    names = [f'shared{i}' for i in range(300)]
    rows = saves._function_rows(net,1,sources=True,names=names)
    assert [r['name'] for r in rows] == names
    assert [r['vram'] for r in rows] == [2**60+i for i in range(300)]
    assert all(r['matched_source'] == source and r['best_source'] is None for r in rows)
    assert calls == [payload_storage.digest(source)]
    assert sizes == [3]  # 300 metadata rows + one distinct compressed body, paged by 128.


def test_selected_metadata_and_payload_share_snapshot(saved_sources, monkeypatch):
    store, net = saved_sources
    before = dict(saves._function_rows(net,1,sources=True,names=['matched'])[0])
    unpack = payload_storage.unpack
    def changing(key, blob):
        with store.tx() as db:
            db.execute("UPDATE functions SET matched_source=?,unit='new unit' WHERE name='matched'",
                       ('/* new body */\n'*100,))
        return unpack(key, blob)
    monkeypatch.setattr(payload_storage, 'unpack', changing)
    for conn in (store.conn,store._reader()):
        conn.create_function('payload_read',2,changing,deterministic=True)
    assert dict(saves._function_rows(net,1,sources=True,names=['matched'])[0]) == before
    monkeypatch.setattr(payload_storage, 'unpack', unpack)
    for conn in (store.conn,store._reader()):
        conn.create_function('payload_read',2,unpack,deterministic=True)
    after = dict(saves._function_rows(net,1,sources=True,names=['matched'])[0])
    assert after['unit'] == 'new unit' and after['matched_source'] != before['matched_source']


def test_missing_selected_payload_fails_instead_of_dropping_source(saved_sources):
    store, net = saved_sources
    with store.tx() as db:
        db.execute("DELETE FROM text_payloads WHERE hash=(SELECT lower(hex(matched_source)) FROM main.functions WHERE name='matched')")
    with pytest.raises(sqlite3.OperationalError, match='user-defined function'):
        saves._function_rows(net,1,sources=True,names=['matched'])


def test_inline_unicode_null_and_tied_addresses_keep_exact_order(saved_sources):
    store, net = saved_sources
    values = [('名字\0λ',None,'短い C\0'),('second',None,''),('same_a',7,'a'),('same_b',7,'b')]
    with store.tx() as db:
        db.executemany("INSERT INTO functions(project_id,name,vram,status,matched_source,unit,meta) "
                       "VALUES(1,?,?,'matched',?,?,?)", [(*row,'unit\0λ','{"note":"λ"}') for row in values])
    names = [r[0] for r in values]
    rows = saves._function_rows(net,1,sources=True,names=names)
    assert [r['name'] for r in rows] == names
    assert [r['matched_source'] for r in rows] == ['短い C\0',None,'a','b']
    assert all(r['unit'] == 'unit\0λ' and r['meta'] == '{"note":"λ"}' for r in rows)
