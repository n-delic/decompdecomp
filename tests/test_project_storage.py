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


def test_claim_receipts_and_release_lifecycle_stay_in_their_project(scoped):
    import json

    from decompnet.app import claim_state
    from decompnet.release_receipts import current

    net, stores = scoped
    catalog_settings = [tuple(r) for r in net.store.all('SELECT * FROM settings ORDER BY key')]
    net.fork_slug = lambda _: None
    agent = Agent(1, 'worker', None)
    for pid in (1, 2):
        with stores[pid].tx() as db:
            db.execute("UPDATE functions SET best_source='int same_name(void) { return 1; }',best_score=20")
    first = net.claim_row(agent, 'first')
    second = net.claim_row(agent, 'second')
    assert (first['id'], second['id']) == (10, 20)
    one, two = current(net, first, agent), current(net, second, agent)
    assert one and two and one != two
    assert json.loads(claim_state.get_claims(net, agent, 'first'))['claims'][0]['claim_token'] == one
    assert json.loads(claim_state.get_claims(net, agent, 'second'))['claims'][0]['claim_token'] == two
    assert '## CLAIMED: second/same_name' in claim_state.brief(net, second, agent, 'CLAIMED')
    assert net.claim_progress(agent, first)['claimed_at'] is not None

    reply = net.release(agent, 'same_name', 'Trying another function.', 'first', claim_token=one)
    assert 'Released' in reply
    assert net.release(agent, 'same_name', 'Trying another function.', 'first', claim_token=one) == reply
    assert current(net, second, agent) == two
    assert stores[1].one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert stores[2].one('SELECT COUNT(*) n FROM claims')['n'] == 1
    assert stores[1].one("SELECT COUNT(*) n FROM settings WHERE key LIKE 'release-receipts:%'")['n'] == 1
    assert stores[2].one("SELECT COUNT(*) n FROM settings WHERE key LIKE 'release-receipts:%'")['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert [tuple(r) for r in net.store.all('SELECT * FROM settings ORDER BY key')] == catalog_settings


def test_quality_list_fans_out_without_catalog_function_or_setting_reads(scoped, monkeypatch):
    import hashlib
    import json

    from decompnet import candidate_quality

    net, stores = scoped
    for pid in (1, 2):
        source = f'int same_name(void) {{ return {pid}; }}'
        report = {'source_hash': hashlib.sha256(source.encode()).hexdigest(),
                  'warning': f'project {pid}', 'alternatives': []}
        with stores[pid].tx() as db:
            db.execute('UPDATE functions SET best_source=?', (source,))
            db.execute('INSERT INTO settings VALUES(?,?)', (candidate_quality.PREFIX+str(pid*10), json.dumps([report])))
    original = net.store.all

    def catalog_only(sql, *args):
        assert sql == 'SELECT slug FROM projects ORDER BY id'
        return original(sql, *args)

    monkeypatch.setattr(net.store, 'all', catalog_only)
    rows = candidate_quality.listing(net)
    assert [(r['slug'], r['warning']) for r in rows] == [('first', 'project 1'), ('second', 'project 2')]
    assert [r['slug'] for r in candidate_quality.listing(net, 'second')] == ['second']


def test_pause_retries_and_candidate_stamps_use_operational_store(scoped):
    from decompnet import candidate_scope, claim_stalls

    net, stores = scoped
    catalog_settings = [tuple(r) for r in net.store.all('SELECT * FROM settings ORDER BY key')]
    net.fork_slug = lambda _: None
    agent = Agent(1, 'worker', None)
    first, second = (net._function('same_name', slug) for slug in ('first', 'second'))
    for pid in (1, 2):
        with stores[pid].tx() as db:
            db.execute("UPDATE functions SET best_source='int same_name(void) { return 1; }',best_score=20")
    first = net._function('same_name', 'first')
    claim_stalls.released(net, first, exhausted=True, agent=agent)
    assert claim_stalls.blocked(net, net._project('first'), agent) == [10]
    assert claim_stalls.blocked(net, net._project('second'), agent) == []
    claim_stalls.resume(net, first, agent, single_turn=True)
    assert claim_stalls.grants(net, net._project('first'), agent) == [10]
    assert claim_stalls.grants(net, net._project('second'), agent) == []
    net.claim_row(agent, 'first')
    assert claim_stalls.grants(net, net._project('first'), agent) == []
    proj = net._project('second')
    stamp = candidate_scope.key(net, proj, second)
    with stores[2].tx() as db:
        db.execute("UPDATE context_versions SET header='int changed_header;'")
    assert candidate_scope.key(net, proj, second) != stamp
    assert [tuple(r) for r in net.store.all('SELECT * FROM settings ORDER BY key')] == catalog_settings


def test_quick_claim_serialization_never_waits_for_the_catalog_mutex(scoped):
    import threading

    from decompnet.app.claim_state import quick

    net, _ = scoped
    held, release = threading.Event(), threading.Event()

    def hold_catalog():
        with net.store.lock:
            held.set()
            assert release.wait(10)

    @quick
    def claim(net, agent, slug):
        return net._function('same_name', slug)['id']

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(hold_catalog)
        try:
            assert held.wait(5)
            result = pool.submit(claim, net, Agent(1, 'worker', None), 'second')
            assert result.result(timeout=5) == 20
        finally:
            release.set()
            writer.result(timeout=5)


def test_scheduling_batch_cannot_reuse_another_projects_header_cache(scoped):
    from decompnet import candidate_scope

    net, _ = scoped
    first, second = net._project('first'), net._project('second')
    batch = candidate_scope.batch_context(net, first)
    assert candidate_scope.context(net, first, '', batch)['header'] == 'int first;'
    with pytest.raises(NetworkError, match='another project or database'):
        candidate_scope.context(net, second, '', batch)
    with pytest.raises(NetworkError, match='another project or database'):
        candidate_scope.state(net, second, net._function('same_name', 'second'), batch)


def test_implicit_claim_packet_recovers_project_scope_from_claim_label(scoped, monkeypatch):
    from decompnet.app import agent_tokens

    net, _ = scoped
    agent = Agent(1, 'worker', None)
    net.claim_row(agent, 'first')
    monkeypatch.setattr(agent_tokens, 'brief', lambda net, text, *args: text)
    text = '## CLAIMED: first/same_name\nCandidate C'
    assert agent_tokens.claim_packet(net, agent, None, text) == text
    assert 'No new functions were claimed' in agent_tokens.claim_packet(net, agent, None, text)


def _saved_projects(net, stores, tmp_path):
    import json

    from decompnet.app.engine.core import Core
    from decompnet.app.knowledge import SCHEMA
    from decompnet.app.progress import Progress

    net.cfg = Core.cfg
    net.is_rom = Core.is_rom.__get__(net)
    for pid, slug in ((1, 'first'), (2, 'second')):
        folder = tmp_path / slug
        folder.mkdir()
        cfg = {'kind': 'rom', 'platform': 'n64', 'rom': {'sha1': f'synthetic-{pid}'}}
        with stores[pid].tx() as db:
            db.execute('UPDATE projects SET root=?,config=?', (str(folder), json.dumps(cfg)))
            db.execute(SCHEMA[0])
    net.progress = Progress(net)
    return net.progress


def test_progress_sync_and_manual_save_only_publish_the_selected_project(scoped, tmp_path, monkeypatch):
    import json

    from decompnet.app import saves

    net, stores = scoped
    progress = _saved_projects(net, stores, tmp_path)
    with stores[1].tx() as db:
        db.execute("UPDATE functions SET best_source='int first_draft;',best_score=10,best_agent_id=1")
        db.execute("INSERT INTO notes(project_id,function_id,agent_id,text) VALUES(1,10,1,'first note')")
        # Agent identity is catalog-owned even if a stale local copy remains.
        db.execute("UPDATE agents SET name='stale identity'")
    with stores[2].tx() as db:
        db.execute("UPDATE functions SET best_source='int second_draft;',best_score=11,best_agent_id=1")
    original = net.store.all

    def identities_only(sql, *args):
        assert sql.startswith('SELECT id,name FROM agents')
        return original(sql, *args)

    monkeypatch.setattr(net.store, 'all', identities_only)
    assert progress.sync('first', force=True)
    assert progress.sync('second', force=True)
    one, two = saves.default_folder(net, 'first'), saves.default_folder(net, 'second')
    assert (one/'best/same_name.c').read_text() == 'int first_draft;\n'
    assert (two/'best/same_name.c').read_text() == 'int second_draft;\n'
    first = json.loads((one/saves.MANIFEST).read_text())
    second = json.loads((two/saves.MANIFEST).read_text())
    assert first['notes'][0]['by'] == 'worker'
    assert first['notes'][0]['text'] == 'first note' and second['notes'] == []
    assert first['functions']['same_name']['best_by'] == 'worker'
    assert stores[1].one('SELECT project_id FROM progress_state')['project_id'] == 1
    assert stores[2].one('SELECT project_id FROM progress_state')['project_id'] == 2
    assert net.store.one('SELECT COUNT(*) n FROM progress_state')['n'] == 0
    before = (two/saves.MANIFEST).read_bytes()
    result = saves.save(net, 'first', tmp_path/'backup')
    assert result['best'] == 1
    assert (tmp_path/'backup/best/same_name.c').read_text() == 'int first_draft;\n'
    assert (two/saves.MANIFEST).read_bytes() == before
    assert saves.save_folder(net, 'first') == (tmp_path/'backup').resolve()
    assert saves.save_folder(net, 'second') == two


def test_autosave_discovers_operational_paths_and_policy_not_catalog_copies(scoped, tmp_path):
    import json

    from decompnet.app.saves import Autosave

    net, stores = scoped
    _saved_projects(net, stores, tmp_path)
    auto = Autosave(net)
    rows = auto._projects()
    assert [r['root'] for r in rows] == [str(tmp_path/'first'), str(tmp_path/'second')]
    with stores[2].tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects').fetchone()['config'])
        cfg['auto_commit_matches'] = True
        db.execute('UPDATE projects SET config=?', (json.dumps(cfg),))
    assert json.loads(auto._projects()[1]['config'])['auto_commit_matches']
    assert net.store.one('SELECT config FROM projects WHERE id=2')['config'] == '{}'


def test_transaction_fence_covers_nested_stores_rollback_and_callbacks(scoped):
    from decompnet.store import in_transaction

    net, stores = scoped
    seen = []
    assert not in_transaction()
    with pytest.raises(RuntimeError, match='cancel'):
        with stores[1].tx():
            assert in_transaction()
            with stores[1].tx():
                assert in_transaction()
            with stores[2].try_tx() as db:
                assert db is not None and in_transaction()
                stores[2].after_commit(lambda: seen.append(in_transaction()))
            assert seen == [True]  # the first project's outer transaction remains open
            with net.store.tx():
                assert in_transaction()
            raise RuntimeError('cancel')
    assert not in_transaction()
    with stores[1].tx():
        stores[1].after_commit(lambda: seen.append(in_transaction()))
    assert seen == [True, False]


def test_transaction_fence_is_thread_local_and_defers_other_project_sync(scoped, tmp_path):
    from decompnet.store import in_transaction

    net, stores = scoped
    progress = _saved_projects(net, stores, tmp_path)

    def other_thread():
        before = in_transaction()
        with stores[2].tx():
            during = in_transaction()
        return before, during, in_transaction()

    with stores[1].tx():
        assert progress.sync('second', force=True) is False
        assert not (tmp_path/'second/progress').exists()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(other_thread).result(timeout=5) == (False, True, False)
        assert in_transaction()
    assert not in_transaction()
    assert progress.sync('second', force=True)


def test_progress_policy_and_score_acknowledgements_use_the_selected_store(scoped, tmp_path):
    from decompnet.app import auto_push, score_commits

    net, stores = scoped
    _saved_projects(net, stores, tmp_path)
    auto_push._change(net, 'second', lambda cfg: cfg.update(instructions='second project'))
    assert net.cfg(net._project('second'))['instructions'] == 'second project'
    assert 'instructions' not in net.cfg(net._project('first'))
    score_commits._acknowledge(net, [{'id':10, 'best_score':8}, {'id':20, 'best_score':9}], project_id=2)
    assert [tuple(r) for r in stores[2].all('SELECT project_id,function_id,score FROM score_commit_baselines')] == [(2, 20, 9)]
    assert stores[1].one('SELECT COUNT(*) n FROM score_commit_baselines')['n'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM score_commit_baselines')['n'] == 0
    assert net.store.one('SELECT config FROM projects WHERE id=2')['config'] == '{}'


def test_saved_verification_history_stays_project_local_and_never_awards_a_match(scoped):
    from decompnet import match_bundle

    net, stores = scoped
    first = net._function('same_name', 'first')
    source = 'int same_name(void) { return 0; }'
    net._record(first, Agent(1, 'worker', None), 0, source, 1, matched=True)
    bundle = match_bundle.portable(net, 1)
    assert bundle['matches']['same_name']['verdict'] == 'exact'
    assert not match_bundle.portable(net, 2)
    match_bundle.restore_evidence_many(net, 2, [('same_name', 'same_name')], {'verification': bundle})
    restored = match_bundle.portable(net, 2)
    assert restored['matches']['same_name']['imported']
    assert source in restored['sources'].values()
    assert stores[2].one('SELECT status,best_source FROM functions')['status'] == 'open'
    assert stores[2].one('SELECT status,best_source FROM functions')['best_source'] is None
    assert net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'") is None


def test_autosave_legacy_catalog_fast_path_does_not_add_per_project_queries(scoped, monkeypatch):
    from decompnet.app.saves import Autosave
    from decompnet.network import Network
    from decompnet.request_timing import capture

    net, _ = scoped
    monkeypatch.setattr(net, 'project_store', Network.project_store.__get__(net))
    auto = Autosave(net)
    auto._projects()  # warm the existing catalog revision cache
    with capture() as timing:
        assert len(auto._projects()) == 2
    assert timing['queries'] == 1  # the catalog revision token, no N+1 path/config lookups


def test_shutdown_flush_discovers_dirty_state_in_each_project_store(scoped, tmp_path, monkeypatch):
    net, stores = scoped
    progress = _saved_projects(net, stores, tmp_path)
    progress.sync('first', force=True)
    progress.sync('second', force=True)
    with stores[1].tx() as db:
        db.execute("UPDATE functions SET best_source='int changed;' WHERE id=10")
    seen = []
    monkeypatch.setattr(progress, 'sync', lambda slug, **kw: seen.append(slug))
    assert progress.flush() == {}
    assert seen == ['first']


def test_match_commit_attribution_reads_project_attempts_and_shared_identities(scoped, tmp_path):
    import json

    from decompnet.app import match_commit_notes, match_commits

    net, stores = scoped
    _saved_projects(net, stores, tmp_path)
    fn = net._function('same_name', 'second')
    net._record(fn, Agent(1, 'worker', None), 5, 'int candidate;', 1, via='permuter')
    aid = stores[2].one('SELECT id FROM attempts')['id']
    with stores[2].tx() as db:
        db.execute("UPDATE agents SET name='stale local name'")
        db.execute("INSERT INTO repair_examples(project_id,function_id,created_at,category,status,explanation,evidence,attempt_id) "
                   "VALUES(2,20,0,'types','accepted','Scoped repair',?,?)",
                   (json.dumps({'after': {'source_sha256': 'source'}}), aid))
        db.execute("UPDATE projects SET config=json_set(config,'$.auto_commit_matches',json('true'))")
        db.execute('INSERT INTO auto_match_commits(id,project_id,function_id,attempt_id,agent_id,created_at,notes_after,snapshot) '
                   "VALUES('job',2,20,?,1,0,0,?)", (aid, json.dumps({
                       'name': 'same_name', 'info': {}, 'details': {'source_sha256': 'source'}})))
    assert match_commit_notes.origin(net, aid, '', project_id=2) == {'agent': 'worker', 'method': 'permuter'}
    state = match_commits.status(net, net._project('second'))
    assert state['pending'] == 1 and state['items'][0]['agent'] == 'worker'
    job = stores[2].one('SELECT * FROM auto_match_commits')
    message = match_commit_notes.message(net, job, None)
    assert 'Matched by: worker' in message and 'Repair explanation: Scoped repair' in message
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == 0
