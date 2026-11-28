"""The permuter worker's engine follows renames made by the app's own engine."""
from types import SimpleNamespace

from decompnet.app.permuter_worker import follow_symbol_edits
from decompnet.store import Store


def test_a_rename_through_another_connection_drops_the_cached_names(tmp_path):
    app, worker = Store(str(tmp_path / 'project.sqlite3')), Store(str(tmp_path / 'project.sqlite3'))
    with app.tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'sTarget',4096,4,'data')")
    net = SimpleNamespace(_syms={}, project_store=lambda pid: worker)

    def symbols(pid):
        if pid not in net._syms:
            net._syms[pid] = [r['name'] for r in worker.all('SELECT name FROM symbols WHERE project_id=?', pid)]
        return net._syms[pid]

    net.symbols = symbols
    follow_symbol_edits(net)
    assert net.symbols(1) == ['sTarget'] and net.symbols(1) is net.symbols(1)
    with app.tx() as db:
        db.execute("UPDATE symbols SET name='gTarget' WHERE project_id=1")
    assert net.symbols(1) == ['gTarget']
