"""A project's row is read once until something writes it (Network._project): a compile looked it up 53 times."""
import json

from decompnet.app.engine import RomNetwork


def test_project_row_is_cached_until_any_column_changes(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                   (str(tmp_path / 'game'), json.dumps({'kind': 'rom', 'big': 'x' * 100_000})))
    store = net.project_store('game')
    reads = []
    store.conn.set_trace_callback(None)
    first = net._project('game')
    reader = store._reader()
    reader.set_trace_callback(reads.append)
    try:
        assert net._project('game') is first
        assert not any('FROM projects' in q for q in reads)  # only the revision was read
        for change in ("UPDATE projects SET description='new' WHERE slug='game'",  # a column no other revision follows
                       "UPDATE projects SET config='{\"kind\": \"rom\"}' WHERE slug='game'"):
            with store.tx() as db:
                db.execute(change)
            again = net._project('game')
            assert again is not first
            first = again
        assert net._project('game')['config'] == '{"kind": "rom"}' and net._project('game')['description'] == 'new'
        with store.tx():  # inside a transaction a read sees the transaction's own writes, never the cache
            store.conn.execute("UPDATE projects SET name='Renamed' WHERE slug='game'")
            assert net._project('game')['name'] == 'Renamed'
        assert net._project('game')['name'] == 'Renamed'
    finally:
        reader.set_trace_callback(None)
        store._release_reader()
