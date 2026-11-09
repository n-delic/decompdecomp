"""Full compiler/judge function records survive compressed snapshot transport."""
import json
import sqlite3

import pytest

from decompnet import function_rows, payload_storage
from tests.test_save_selected_sources import saved_sources  # noqa: F401


def legacy(store, project, ids):
    return [dict(r) for r in store.all('SELECT * FROM functions WHERE project_id=? '
        'AND id IN (SELECT value FROM json_each(?)) ORDER BY id', project, json.dumps(ids))]


def test_complete_rows_keep_types_payloads_and_scope(saved_sources):  # noqa: F811
    store, _ = saved_sources
    body = '/* synthetic λ */\n' * 100
    with store.tx() as db:
        db.execute("UPDATE functions SET asm=?,seed=?,best_source=?,unpolished_source=?,target=?,vram=?,"
                   "best_score=?,meta=?,unit=? WHERE name='matched'",
                   (body,body,body,body,bytes(range(256)),2**60+1,1.0000000000000002,'{}','λ\0model'))
        db.execute("UPDATE functions SET best_score=? WHERE name='inline'", (1794217763.152982,))
    ids = [r['id'] for r in store.all('SELECT id FROM main.functions')]
    expected = legacy(store, 1, ids)
    actual = function_rows.selected(store, 1, ids)
    assert actual == expected
    assert [[type(v) for v in r.values()] for r in actual] == [[type(v) for v in r.values()] for r in expected]
    assert function_rows.selected(store, 2, ids) == legacy(store, 2, ids)
    assert function_rows.selected(store, 1, []) == []


def test_shared_bodies_decode_once_and_cross_in_pages(saved_sources, monkeypatch):  # noqa: F811
    store, _ = saved_sources
    body = 'int shared_synthetic_body;\n' * 100
    with store.tx() as db:
        db.executemany("INSERT INTO functions(project_id,name,status,asm,seed,matched_source,best_source) "
                       "VALUES(1,?,'matched',?,?,?,?)", [(f'batch{i}',body,body,body,body) for i in range(300)])
    ids = [r['id'] for r in store.all("SELECT id FROM main.functions WHERE name LIKE 'batch%' ORDER BY id")]
    expected = legacy(store, 1, ids)
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
    assert function_rows.selected(store, 1, ids) == expected
    assert calls == [payload_storage.digest(body)]
    assert sizes == [3]


def test_snapshot_survives_write_during_unpack_and_transaction_rollback(saved_sources, monkeypatch):  # noqa: F811
    store, _ = saved_sources
    fid = store.one("SELECT id FROM main.functions WHERE name='matched'")['id']
    before = legacy(store, 1, [fid])
    unpack = payload_storage.unpack
    def changing(key, blob):
        with store.tx() as db:
            db.execute('UPDATE functions SET target=?,unit=? WHERE id=?', (b'new target','new unit',fid))
        return unpack(key, blob)
    monkeypatch.setattr(payload_storage, 'unpack', changing)
    assert function_rows.selected(store, 1, [fid]) == before
    monkeypatch.setattr(payload_storage, 'unpack', unpack)
    after = legacy(store, 1, [fid])
    assert after != before and function_rows.selected(store, 1, [fid]) == after
    with pytest.raises(ValueError, match='rollback'):
        with store.tx() as db:
            db.execute("UPDATE functions SET matched_source='transaction' WHERE id=?", (fid,))
            assert function_rows.selected(store, 1, [fid])[0]['matched_source'] == 'transaction'
            raise ValueError('rollback')
    assert function_rows.selected(store, 1, [fid]) == after


def test_damaged_selected_field_fails_and_other_project_is_ignored(saved_sources):  # noqa: F811
    store, _ = saved_sources
    fid = store.one("SELECT id FROM main.functions WHERE name='matched'")['id']
    with store.tx() as db:
        db.execute("UPDATE text_payloads SET payload=X'00' WHERE hash=(SELECT lower(hex(best_source)) FROM main.functions WHERE id=?)", (fid,))
    assert function_rows.selected(store, 2, [fid]) == []
    with pytest.raises(sqlite3.OperationalError, match='user-defined function'):
        function_rows.selected(store, 1, [fid])
