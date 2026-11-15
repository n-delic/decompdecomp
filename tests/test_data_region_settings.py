"""Data region discovery stays scoped and cheap as unrelated settings accumulate."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from decompnet.app.data_regions import records


@pytest.fixture
def settings():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
    # Other projects and large progress/settings records share this table.
    db.executemany('INSERT INTO settings VALUES(?,?)',
                   [(f'progress:{i:06d}', 'unrelated') for i in range(20000)])
    class Store:
        def all(self, sql, *args):
            return db.execute(sql, args).fetchall()
    requested = []
    def project_store(pid):
        requested.append(pid)
        return Store()
    yield db, SimpleNamespace(project_store=project_store), requested
    db.close()


def test_region_records_preserve_namespace_status_and_freshness(settings):
    db, net, requested = settings
    items = [('data-region:1:a', {'status':'paused'}),
             ('data-region:1:b', {'status':'dismissed'}),
             ('data-region:1:c', {'status':'review'})]
    db.executemany('INSERT INTO settings VALUES(?,?)',
                   [(key, json.dumps(value)) for key, value in items]+
                   [('data-region:10:a', 'not JSON'), ('data-region:2:a', 'not JSON'),
                    ('data-region:1', 'not JSON'), ('data-region:1;a', 'not JSON')])
    assert sorted(records(net, 1)) == items
    assert records(net, 3) == []
    db.execute('UPDATE settings SET value=? WHERE key=?',
               (json.dumps({'status':'review'}), items[0][0]))
    assert dict(records(net, 1))[items[0][0]] == {'status':'review'}
    db.execute('DELETE FROM settings WHERE key=?', (items[1][0],))
    assert items[1][0] not in dict(records(net, 1))
    assert requested == [1, 3, 1, 1]


def test_region_lookup_does_not_scan_unrelated_settings(settings):
    db, net, _ = settings
    db.execute('INSERT INTO settings VALUES(?,?)', ('data-region:1:a', '{"status":"paused"}'))
    steps = 0
    def progress():
        nonlocal steps
        steps += 100
        return 0
    db.set_progress_handler(progress, 100)
    try:
        assert records(net, 1) == [('data-region:1:a', {'status':'paused'})]
        assert records(net, 2) == []
    finally:
        db.set_progress_handler(None, 0)
    # A full scan of the 20k unrelated keys takes >160k VM instructions.
    assert steps < 1000
