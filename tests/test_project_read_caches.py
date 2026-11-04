"""Derived UI caches invalidate on real inputs, not every poll or unrelated bookkeeping."""
import json
import logging
import os
from pathlib import Path

import pytest

from decompnet import claim_stalls
from decompnet.app.profile_hypotheses import captured
from decompnet.app.startup import StartupQueue
from decompnet.read_cache import ReadCache

from .test_app_server import app  # noqa: F401
from .test_claims import _game


def test_read_cache_single_result_version_and_transaction_rollback(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    store = net.store
    calls = []
    def read():
        calls.append(1)
        return store.one('SELECT name FROM functions WHERE id=1')['name']
    def get():
        return store.read_cache.get('name', store.read_version('inputs'), read)
    assert get() == get() == 'f' and len(calls) == 1
    with pytest.raises(ValueError):
        with store.tx() as db:
            db.execute("UPDATE functions SET name='uncommitted' WHERE id=1")
            assert get() == 'uncommitted'
            raise ValueError('rollback')
    assert get() == 'f'
    with store.tx() as db:
        db.execute("UPDATE functions SET name='saved' WHERE id=1")
    assert get() == 'saved'


def test_map_cache_skips_targets_until_actual_bytes_change(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,root,arch,big_endian,config) VALUES('other',?,'ppc',1,'{}')", (str(tmp_path),))
        db.execute("UPDATE functions SET target=X'01020304' WHERE id=1")
        db.execute("INSERT INTO functions(project_id,name,target,size,n_insns) VALUES(2,'peer',X'01020304',4,1)")
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    assert net.function_map('game')[0]['cross_project']
    count = lambda: sum('SELECT f.id,f.project_id,f.target' in q for q in queries)
    assert count() == 1
    assert net.function_map('other')[0]['cross_project'] and count() == 1
    net.function_map('game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=1")
    assert net.function_map('game')[0]['cross_project'] and count() == 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=X'05060708' WHERE project_id=2")
    assert not net.function_map('game')[0]['cross_project'] and count() == 2
    net.store._reader().set_trace_callback(None)


def test_captured_profiles_cache_extraction_across_flag_requests(tmp_path):
    net, _ = _game(tmp_path, [('f', 40, 'open')])
    def evidence(flags):
        return json.dumps({'before': {'source_sha256': 'same'}, 'after': {'source_sha256': 'same',
            'settings': {'compiler': {'toolchain': 'gcc-test', 'flags': flags}}}})
    with net.store.tx() as db:
        db.execute('INSERT INTO repair_examples(project_id,function_id,created_at,evidence) VALUES(1,1,0,?)', (evidence(['-O1']),))
    queries = []
    net.store._reader().set_trace_callback(queries.append)
    assert captured(net, {'id': 1}, {'toolchain': 'gcc-test', 'flags': ['-O2']}) == [['-O1']]
    assert captured(net, {'id': 1}, {'toolchain': 'gcc-test', 'flags': ['-O1']}) == []
    assert sum('SELECT json_extract(evidence' in q for q in queries) == 1
    with net.store.tx() as db:
        db.execute('UPDATE repair_examples SET evidence=?', (evidence(['-O3']),))
    assert captured(net, {'id': 1}, {'toolchain': 'gcc-test', 'flags': ['-O2']}) == [['-O3']]
    net.store._reader().set_trace_callback(None)


def test_repair_code_digest_reuses_bytes_and_detects_preserved_mtime(tmp_path, monkeypatch):
    file = tmp_path / 'rule.py'
    file.write_text('first')
    calls = []
    original = Path.read_bytes
    def counted(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', counted)
    first = claim_stalls.file_digest(file)
    assert claim_stalls.file_digest(file) == first and len(calls) == 1
    old = file.stat()
    file.write_text('other')
    os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert claim_stalls.file_digest(file) != first and len(calls) == 2


def test_startup_staggers_work_yields_but_does_not_starve():
    now = [0.0]
    queue = StartupQueue(logging.getLogger('test'), clock=lambda: now[0])
    def wait(seconds):
        now[0] += seconds
        queue.touch()  # Continuous navigation may defer a task only up to the bound.
    queue.wait = wait
    seen = []
    queue.add('first', lambda: seen.append(now[0]))
    queue.add('second', lambda: seen.append(now[0]))
    queue.run()
    assert 9.9 <= seen[0] <= 10.1 and seen[1]-seen[0] >= 4.9
    queue.close()
    assert not queue.ready(0)


def test_read_cache_failure_is_not_cached():
    cache = ReadCache()
    with pytest.raises(ValueError):
        cache.get('key', 1, lambda: (_ for _ in ()).throw(ValueError()))
    assert cache.get('key', 1, lambda: 2) == 2


def test_compiler_get_returns_while_automatic_checks_are_still_running(app, monkeypatch):  # noqa: F811
    from threading import Event
    client, net, _ = app
    entered, release = Event(), Event()
    checks = []
    def answer(slug, check_now=False, *, display_only=False):
        if display_only:
            return {'display': True}
        checks.append(check_now)
        if not check_now:
            entered.set()
            assert release.wait(5)
        return {'explicit': check_now}
    monkeypatch.setattr(net, 'compiler_answer', answer)
    try:
        assert client.get('/api/compilermap?slug=game').json() == {'display': True}
        assert entered.wait(5)
        assert client.get('/api/compilermap?slug=game').json() == {'display': True}
        assert checks == [False]
        assert client.post('/api/compilermap', json={'slug': 'game'}, headers={'X-Decomp': '1'}).json() == {'explicit': True}
    finally:
        release.set()


def test_compiler_rows_reuse_metadata_across_source_edits(tmp_path):
    net, pid = _game(tmp_path, [('f', 40, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE functions SET vram=4096 WHERE id=1')
    proj = net.project_row('game')
    first = net.rangechecks._rows(proj)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='new candidate' WHERE id=1")
    assert net.rangechecks._rows(proj) is first
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=1")
    assert net.rangechecks._rows(proj)[0]['status'] == 'matched'
    query = 'SELECT id,name,vram,size,n_insns,status FROM functions WHERE project_id=? AND vram IS NOT NULL ORDER BY vram'
    assert any('COVERING INDEX idx_functions_compiler_rows' in r['detail'] for r in net.store.all('EXPLAIN QUERY PLAN '+query, pid))


def test_deferred_threads_have_explanations():
    from decompnet.app.thread_activity import purpose
    assert purpose('startup-maintenance')[0] == 'Startup maintenance'
    assert purpose('compiler-status-project')[0] == 'Compiler evidence'
