"""Exercise the storage boundary with actual independent SQLite files.

The application still selects the legacy Store. These tests supply a resolver to
catch accidental catalog reads/writes before automatic migration is enabled.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.network import Agent, Network, NetworkError


@pytest.fixture
def scoped(tmp_path):
    catalog = Network(tmp_path / 'catalog.sqlite3')
    projects = {}
    for pid, slug in ((1, 'first'), (2, 'second')):
        folder = tmp_path / 'projects' / slug
        folder.mkdir(parents=True)
        leaf = Network(folder / 'project.sqlite3')
        projects[pid] = projects[slug] = leaf.store
        for store in (catalog.store, leaf.store):
            with store.tx() as db:
                db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(?,?,?,'mips','.','{}')",
                           (pid, slug, slug))
                db.execute("INSERT OR IGNORE INTO agents(id,name,token_hash) VALUES(1,'worker','token')")
        with leaf.store.tx() as db:
            db.execute("INSERT INTO functions(id,project_id,name,n_insns,target,relocs,asm) "
                       "VALUES(?,?,'same_name',1,?,'[]','nop')", (pid * 10, pid, b'\0' * 4))
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,?,'')",
                       (pid, f'int {slug};'))
        leaf._pool.shutdown()
    # This override is test-only. There is no runtime switch to route partially
    # ported services to shards, nor a silent fallback for an unknown project.
    catalog.project_store = projects.__getitem__
    yield catalog, projects
    catalog._pool.shutdown()
    for store in {catalog.store, *projects.values()}:
        store.conn.close()


def test_legacy_resolver_needs_explicit_project_but_keeps_existing_file(tmp_path):
    net = Network(tmp_path / 'legacy.sqlite3')
    try:
        assert net.project_store('first') is net.store
        assert net.project_store(1) is net.store
        for missing in (None, '', ' ', 0, -1, True):
            with pytest.raises(ValueError, match='Project storage needs'):
                net.project_store(missing)
    finally:
        net._pool.shutdown()
        net.store.conn.close()


def test_function_lookup_and_unit_headers_use_selected_store(scoped):
    net, stores = scoped
    assert net._function('same_name', 'first')['id'] == 10
    assert net._function('second/same_name', None)['id'] == 20
    assert net._context(1)['header'] == 'int first;'
    assert net._context(2, 'missing-unit')['header'] == 'int second;'
    with stores[1].tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,2,'changed','source.c')")
    assert net._unit_contexts(1) == {'source.c'}
    assert net._unit_contexts(2) == set()
    assert net._context(1, 'source.c')['header'] == 'changed'
    with pytest.raises(NetworkError, match='no function'):
        net._function('missing', 'first')
    assert net.store.one('SELECT COUNT(*) n FROM functions')['n'] == 0


def test_implicit_single_project_reads_operational_row_not_catalog_snapshot(scoped):
    net, stores = scoped
    with net.store.tx() as db:
        db.execute('DELETE FROM projects WHERE id=2')
    with stores[1].tx() as db:
        db.execute("UPDATE projects SET name='Updated name'")
    assert net._project(None)['name'] == 'Updated name'
    with stores[1].tx() as db:
        db.execute('DELETE FROM projects')
    with pytest.raises(NetworkError, match='Restore it from backup'):
        net._project(None)


def test_concurrent_notes_and_selection_never_change_other_project(scoped):
    net, stores = scoped
    agent = Agent(1, 'worker', None)

    def write(slug):
        net.add_note(agent, slug, f'{slug} note', 'same_name', score_before=9, score_after=9)
        net.set_selection(slug, agent.name, {'max_tries': 7})
        return net.search_notes(slug, 'note')

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ('first', 'second')))
    assert 'first note' in results[0] and 'second note' not in results[0]
    assert 'second note' in results[1] and 'first note' not in results[1]
    for pid in (1, 2):
        row = stores[pid].one('SELECT project_id,score_before,score_after FROM notes')
        assert tuple(row) == (pid, 9, 9)
        assert net.selection('first' if pid == 1 else 'second', 'worker')['max_tries'] == 7
    assert net.store.one('SELECT COUNT(*) n FROM notes')['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM agent_selection')['n'] == 0


def test_attempt_transaction_and_callback_belong_to_project_store(scoped):
    net, stores = scoped
    fn = net._function('same_name', 'first')
    agent = Agent(1, 'worker', None)
    notifications = []
    net.on_match = lambda proj, fid, who: notifications.append((proj['slug'], fid, who.id))
    net.on_external_match = lambda *args: notifications.append(args)
    with stores[1].tx() as db:
        db.execute("INSERT INTO agents(id,name,token_hash) VALUES(2,'other-worker','other-token')")
        db.execute('INSERT INTO claims VALUES(10,2,1,9999999999)')

    # _record's nested transaction defers publishing until its owning outer
    # transaction succeeds, rather than enrolling on the idle catalog Store.
    with pytest.raises(RuntimeError, match='cancel'):
        with stores[1].tx():
            net._record(fn, agent, 0, 'int same_name(void) { return 0; }', 1, matched=True)
            assert notifications == []
            raise RuntimeError('cancel')
    assert notifications == []
    assert stores[1].one('SELECT COUNT(*) n FROM attempts')['n'] == 0
    assert stores[1].one('SELECT status FROM functions')['status'] == 'open'
    net._record(fn, agent, 0, 'int same_name(void) { return 0; }', 1, matched=True)
    assert ('first', 10, 1) in notifications
    assert (2, 'same_name', 1) in notifications
    assert stores[1].one('SELECT attempts,status FROM functions')['status'] == 'matched'
    assert stores[2].one('SELECT attempts,status FROM functions')['attempts'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == 0


def test_core_symbol_reads_and_config_writes_use_project_store(scoped):
    from types import SimpleNamespace

    from decompnet.app.engine.core import Core

    net, stores = scoped
    net._syms, net._sym_override = {}, {}
    net.progress = SimpleNamespace(sync=lambda *args, **kwargs: None)
    with stores[2].tx() as db:
        db.execute("INSERT INTO symbols(project_id,name,address) VALUES(2,'value',4096)")
    assert Core.symbols(net, 2).resolve('value') == 4096
    assert Core._update_cfg(net, 'second', lambda cfg: cfg.update(marker='second')) == {'marker': 'second'}
    assert stores[1].one('SELECT config FROM projects')['config'] == '{}'
    assert net.store.one('SELECT config FROM projects WHERE id=2')['config'] == '{}'


def test_another_projects_writer_does_not_block_scoped_note(scoped):
    import threading

    net, stores = scoped
    held, release = threading.Event(), threading.Event()

    def hold_first():
        with stores[1].tx():
            held.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(hold_first)
        try:
            assert held.wait(5)
            note = pool.submit(net.add_note, Agent(1, 'worker', None), 'second', 'second note', 'same_name')
            assert 'Noted' in note.result(timeout=5)
            assert stores[2].one('SELECT text FROM notes')['text'] == 'second note'
        finally:
            release.set()
            writer.result(timeout=5)


def test_shared_repair_and_reuse_locations_do_not_follow_project_store(scoped):
    from decompnet.app.match_library import Library
    from decompnet.repair_archive import archive_path

    net, stores = scoped
    library = Library(net)
    assert library.path == archive_path(net.store.path).parent / 'match-library.sqlite3'
    assert archive_path(net.store.path) != archive_path(stores[1].path)
    assert library.path.parent != archive_path(stores[1].path).parent
