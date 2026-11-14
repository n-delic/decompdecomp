"""Real guarded HTTP, signed reviewed recipes and existing judges; only tools are stand-ins."""
import hashlib
import json
import threading
import time
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

from decompnet.app.support import Jobs
from decompnet.reference_batches import ReferenceBatches

from .test_app_server import PORT
from .test_reference_trial_app_http import HEADERS, http_trial, prepare, submit
from .test_reference_trial_core import CANDIDATE, ORIGINAL_BYTES


def reviewed(tmp_path, monkeypatch, mode):
    client, net, core, body, calls = http_trial(tmp_path, monkeypatch, mode, match=False)
    plan = prepare(client, body)
    response = submit(client, body, plan)
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == 'nonmatch', response.text
    return client, net, core, body, calls, plan


def preview_batch(client, slug, **limits):
    response = client.post('/api/reference-batches/preview', headers=HEADERS, json={'slug': slug, **limits})
    assert response.status_code == 200, response.text
    return response.json()


def run_batch(client, slug, preview, **changes):
    body = {'slug': slug, 'plan': preview['plan'], 'candidate_ids': [row['id'] for row in preview['candidates']],
            'accept_if_verified': True, 'allow_progress_copy': True, **changes}
    return client.post('/api/reference-batches/run', headers=HEADERS, json=body)


def finished(client, response):
    assert response.status_code == 200, response.text
    data = response.json()
    if data.get('job'):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = client.get('/api/job', params={'id': data['job']}).json()
            if job['state'] != 'running':
                assert job['state'] == 'done', job
                break
            time.sleep(0.01)
        else:
            pytest.fail('Synthetic batch job did not finish.')
    return data


def detail(client, slug, batch_id):
    response = client.get('/api/reference-batches/detail', params={'slug': slug, 'id': batch_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_batch_discovery_never_makes_recipes_from_matching_names_or_previews(tmp_path, monkeypatch, mode):
    client, net, core, body, calls = http_trial(tmp_path, monkeypatch, mode)
    prepare(client, body)  # preparation alone is not a previously consented trial
    before = list(net.store.conn.iterdump())
    files = {str(path): path.read_bytes() for path in (tmp_path / 'game').rglob('*') if path.is_file()}
    preview = preview_batch(client, body['slug'])
    assert preview['candidates'] == []
    assert not calls and not core['events']
    assert list(net.store.conn.iterdump()) == before
    assert files == {str(path): path.read_bytes() for path in (tmp_path / 'game').rglob('*') if path.is_file()}


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_signed_reviewed_batch_uses_actual_judge_and_full_key_miss_cache(tmp_path, monkeypatch, mode):
    client, net, core, body, calls, original = reviewed(tmp_path, monkeypatch, mode)
    slug = body['slug']
    before = list(net.store.conn.iterdump())
    count = len(calls)
    preview = preview_batch(client, slug)
    assert len(preview['candidates']) == 1, preview
    candidate = preview['candidates'][0]
    assert candidate['source'] == original['source'] and candidate['prelude'] == original['prelude']
    assert list(net.store.conn.iterdump()) == before and len(calls) == count
    assert run_batch(client, slug, preview, allow_progress_copy=False).status_code == 400
    assert run_batch(client, slug, preview, accept_if_verified=1).status_code == 400
    assert run_batch(client, slug, preview, candidate_ids=['trial:999999']).status_code == 400
    assert run_batch(client, slug, preview, compiler={'cmd': ['never-run']}).status_code == 400
    assert len(calls) == count
    response = run_batch(client, slug, preview)
    started = finished(client, response)
    value = detail(client, slug, started['batch']['id'])
    assert value['batch']['state'] == 'done', value
    assert value['batch']['matched'] == 0
    assert value['items'][0]['state'] == 'nonmatch', value
    assert len(calls) > count
    count = len(calls)
    finished(client, run_batch(client, slug, preview))
    assert len(calls) == count
    assert net.store.one('SELECT status FROM functions WHERE id=?', body['target_id'])['status'] == 'open'
    if mode == 'fork':
        assert value['items'][0]['percent'] == 100.0  # percent alone is not acceptance
        assert core['path'].read_bytes() == ORIGINAL_BYTES
    cached = preview_batch(client, slug)
    assert not cached['candidates'], cached
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=? WHERE id=?', (b'new original target bytes', body['target_id']))
    changed = preview_batch(client, slug)
    assert changed['candidates'], changed  # old context_hash alone did not bind target bytes
    assert net.store.one('SELECT agent_access FROM reference_sources WHERE id=?', body['origin']['source_id'])[0] == 0
    assert net.store.one('SELECT 1 FROM claims') is None


@pytest.mark.parametrize('mode', ['rom', 'fork'])
@pytest.mark.parametrize('change', ['header', 'reference'])
def test_batch_does_not_silently_rebase_after_review(tmp_path, monkeypatch, mode, change):
    client, net, core, body, calls, _ = reviewed(tmp_path, monkeypatch, mode)
    preview = preview_batch(client, body['slug'])
    count = len(calls)
    with net.store.tx() as db:
        if change == 'header':
            pid = db.execute('SELECT project_id FROM functions WHERE id=?', (body['target_id'],)).fetchone()[0]
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,2,'typedef long T;','')", (pid,))
        else:
            db.execute('UPDATE reference_sources SET stale=1 WHERE id=?', (body['origin']['source_id'],))
    response = run_batch(client, body['slug'], preview)
    if response.status_code == 200:
        started = finished(client, response)
        value = detail(client, body['slug'], started['batch']['id'])
        assert not value['batch']['matched']
        assert all(item['state'] in {'stale', 'skipped', 'error'} for item in value['items']), value
    else:
        assert response.status_code == 400, response.text
    assert len(calls) == count
    assert net.store.one('SELECT 1 FROM claims') is None
    if mode == 'fork':
        assert core['path'].read_bytes() == ORIGINAL_BYTES


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_batch_window_write_and_room_guards_precede_effects(tmp_path, monkeypatch, mode):
    client, net, core, body, calls, _ = reviewed(tmp_path, monkeypatch, mode)
    slug = body['slug']
    anon = TestClient(client.app, base_url=f'http://127.0.0.1:{PORT}')
    assert anon.get('/api/reference-batches', params={'slug': slug}).status_code == 401
    assert client.post('/api/reference-batches/preview', json={'slug': slug}).status_code == 403
    for limits in ({'max_trials': True}, {'max_trials': 21}, {'max_seconds': False}, {'max_seconds': 3601}):
        assert client.post('/api/reference-batches/preview', headers=HEADERS, json={'slug': slug, **limits}).status_code == 400
    count = len(calls)
    for attribute in ('rooms', 'links'):
        original = getattr(net, attribute)
        setattr(net, attribute, {slug: object()})
        try:
            assert client.post('/api/reference-batches/preview', headers=HEADERS, json={'slug': slug}).status_code == 400
            assert client.get('/api/reference-batches', params={'slug': slug}).status_code == 400
        finally:
            setattr(net, attribute, original)
    assert len(calls) == count


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_stop_and_generic_job_cancel_cannot_hide_an_inflight_match(tmp_path, monkeypatch, mode):
    client, net, core, body, calls, _ = reviewed(tmp_path, monkeypatch, mode)
    slug = body['slug']
    preview = preview_batch(client, slug)
    entered, release = threading.Event(), threading.Event()
    if mode == 'rom':
        compile_original = net._compile
        def block(*args):
            entered.set()
            assert release.wait(5)
            return compile_original(*args)
        net._compile = block
        net._evaluate = lambda *_args: (object(), True, None)
    else:
        build_original = core['engine']._build_unit
        diff_original = core['fp'].diff
        def block(*args):
            entered.set()
            assert release.wait(5)
            return build_original(*args)
        def matched(*args):
            result = diff_original(*args)
            result.matched = result.bytes_match = True
            return result
        core['engine']._build_unit = block
        core['fp'].diff = matched
    response = run_batch(client, slug, preview)
    try:
        assert response.status_code == 200, response.text
        assert entered.wait(5)
        started = response.json()
        generic = client.post('/api/job/cancel', headers=HEADERS, json={'id': started['job']})
        assert generic.status_code == 200 and generic.json()['cancelled'] is False
        stop = client.post('/api/reference-batches/stop', headers=HEADERS, json={'slug': slug, 'id': started['batch']['id']})
        assert stop.status_code == 200, stop.text
        assert stop.json()['batch']['stop_requested'] is True
    finally:
        release.set()
    finished(client, response)
    value = detail(client, slug, started['batch']['id'])
    assert value['batch']['state'] in {'done', 'stopped'}, value
    assert value['batch']['matched'] == 1 and value['items'][0]['state'] == 'matched', value
    assert net.store.one('SELECT status FROM functions WHERE id=?', body['target_id'])['status'] == 'matched'
    assert net.store.one('SELECT 1 FROM claims') is None


def test_jobs_opt_out_preserves_results_without_changing_ordinary_cancellation():
    jobs = Jobs()
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    def work(_log):
        entered.set()
        try:
            assert release.wait(5)
            return {'matched': True}
        finally:
            ended.set()
    job = jobs.start('reference-batch', work, cancellable=False)
    try:
        assert entered.wait(5)
        assert jobs.cancel(job) is False
    finally:
        release.set()
    assert ended.wait(5)
    deadline = time.monotonic() + 5
    while jobs.jobs[job]['state'] == 'running' and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs.jobs[job]['state'] == 'done' and jobs.jobs[job]['result'] == {'matched': True}


def direct_batch(net, slug):
    service = ReferenceBatches(net)
    preview = service.preview(slug)
    assert preview['candidates'], preview
    reservation = service.admit(slug, preview['plan'], [item['id'] for item in preview['candidates']], True, True)
    return service, reservation['batch']['id']


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_unexpected_postcommit_error_and_source_deletion_preserve_the_match(tmp_path, monkeypatch, mode):
    _client, net, core, body, _calls, _plan = reviewed(tmp_path, monkeypatch, mode)
    slug = body['slug']
    service, batch_id = direct_batch(net, slug)
    if mode == 'rom':
        net._evaluate = lambda *_args: (object(), True, None)
    else:
        old_diff = core['fp'].diff
        def match(*args):
            result = old_diff(*args)
            result.matched = result.bytes_match = True
            return result
        core['fp'].diff = match
    original = service._save_item
    failed = False
    def fail_after_commit(batch, index, item):
        nonlocal failed
        if item['state'] == 'matched' and not failed:
            failed = True
            with net.store.tx() as db:
                db.execute('DELETE FROM reference_sources WHERE id=?', (body['origin']['source_id'],))
            raise AssertionError('synthetic failure after the authoritative commit')
        return original(batch, index, item)
    service._save_item = fail_after_commit
    with pytest.raises(AssertionError, match='after the authoritative commit'):
        service.execute(slug, batch_id, lambda _line: None)
    result = service.detail(slug, batch_id)
    assert result['batch']['state'] == 'error' and result['batch']['matched'] == 1, result
    assert result['items'][0]['state'] == 'matched'
    assert net.store.one('SELECT status FROM functions WHERE id=?', body['target_id'])['status'] == 'matched'


def test_cache_write_failure_does_not_hide_a_recorded_native_miss(tmp_path, monkeypatch):
    _client, net, _core, body, _calls, _plan = reviewed(tmp_path, monkeypatch, 'rom')
    service, batch_id = direct_batch(net, body['slug'])
    original_tx = net.store.tx
    class CacheFailure:
        def __init__(self, db):
            self.db = db
        def execute(self, sql, *args):
            if sql.startswith('INSERT OR REPLACE INTO reference_batch_misses'):
                raise RuntimeError('synthetic miss-cache write failure')
            return self.db.execute(sql, *args)
    @contextmanager
    def tx():
        with original_tx() as db:
            yield CacheFailure(db)
    net.store.tx = tx
    result = service.execute(body['slug'], batch_id, lambda _line: None)
    assert result['batch']['state'] == 'done'
    assert result['items'][0]['state'] == 'nonmatch'
    assert net.store.one('SELECT COUNT(*) FROM reference_trials')[0] == 2


def test_two_workers_cannot_both_execute_the_same_queued_batch(tmp_path, monkeypatch):
    _client, net, _core, body, _calls, _plan = reviewed(tmp_path, monkeypatch, 'rom')
    first, batch_id = direct_batch(net, body['slug'])
    second = ReferenceBatches(net)
    barrier, errors = threading.Barrier(2), []
    for service in (first, second):
        original_row = service._row
        seen = set()
        def synchronized(slug, ident, original=original_row, threads=seen):
            row = original(slug, ident)
            current = threading.get_ident()
            if current not in threads:
                threads.add(current)
                barrier.wait(timeout=5)
            return row
        service._row = synchronized
    def execute(service):
        try:
            service.execute(body['slug'], batch_id, lambda _line: None)
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=execute, args=(service,)) for service in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(8)
        assert not thread.is_alive()
    assert not errors, errors
    assert net.store.one('SELECT COUNT(*) FROM reference_trials')[0] == 2
    # Use a fresh service because the test-only barrier belongs to the workers.
    result = ReferenceBatches(net).detail(body['slug'], batch_id)
    assert result['batch']['state'] == 'done' and result['items'][0]['state'] == 'nonmatch', result


def test_prior_accepted_project_edit_requires_review_of_the_remaining_recipe(tmp_path, monkeypatch):
    client, net, core, body, _calls = http_trial(tmp_path, monkeypatch, 'fork', match=False)
    slug = body['slug']
    core['path'].write_bytes(ORIGINAL_BYTES + b'\r\nint neighbor_fn(void) { return 0; }\r\n')
    second_code = CANDIDATE.replace('trial_fn', 'neighbor_fn')
    with net.store.tx() as db:
        pid = db.execute('SELECT project_id FROM functions WHERE id=?', (body['target_id'],)).fetchone()[0]
        fid = db.execute('INSERT INTO functions(project_id,name,status,n_insns,unit,meta) VALUES(?,?,?,?,?,?)',
                         (pid, 'neighbor_fn', 'open', 1, 'unit', json.dumps({'symbol': 'neighbor_fn'}))).lastrowid
        digest = hashlib.sha256(second_code.encode()).hexdigest()
        db.execute('INSERT INTO reference_files(source_id,path,text,content_hash) VALUES(?,?,?,?)',
                   (body['origin']['source_id'], 'neighbor.c', second_code, digest))
    core['engine']._fn = lambda _slug, name: net.store.one('SELECT * FROM functions WHERE project_id=? AND name=?', pid, name)
    neighbor = {'slug': slug, 'target_id': fid, 'origin': {**body['origin'], 'path': 'neighbor.c', 'content_hash': digest}}
    for selected in (body, neighbor):
        response = submit(client, selected, prepare(client, selected))
        assert response.status_code == 200 and response.json()['outcome'] == 'nonmatch', response.text
    preview = preview_batch(client, slug)
    assert len(preview['candidates']) == 2, preview
    old_diff = core['fp'].diff
    def match(*args):
        result = old_diff(*args)
        result.matched = result.bytes_match = True
        return result
    core['fp'].diff = match
    started = finished(client, run_batch(client, slug, preview))
    value = detail(client, slug, started['batch']['id'])
    assert value['batch']['matched'] == 1, value
    assert [item['state'] for item in value['items']] == ['matched', 'stale'], value
    assert b'return 1;' in core['path'].read_bytes()
    assert net.store.one('SELECT 1 FROM claims') is None
