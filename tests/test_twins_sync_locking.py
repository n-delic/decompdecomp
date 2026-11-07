"""Shared fingerprint indexing must not wait on another thread's progress synchronization."""
import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork


@pytest.mark.parametrize('kind', ['rom', 'fork'])
def test_source_matches_reuse_target_prints_without_progress_sync(monkeypatch, kind):
    from decompnet.app import twins
    from decompnet.read_cache import ReadCache

    clock = [1000.]
    monkeypatch.setattr('decompnet.read_cache.time.monotonic', lambda: clock[0])
    monkeypatch.setattr(twins.time, 'sleep', lambda _: None)
    monkeypatch.setattr('decompnet.app.workers.lower_priority', lambda: None)
    monkeypatch.setattr('decompnet.app.maintenance.slot', lambda: nullcontext())
    class InlineThread:
        def __init__(self, *, target, **kwargs):self.target = target
        def start(self):self.target()
    monkeypatch.setattr(twins.threading, 'Thread', InlineThread)
    store = SimpleNamespace(_local=threading.local(), read_cache=ReadCache(), read_version=lambda *a: 1)
    net = SimpleNamespace(store=store, links={}, cfg=lambda proj: {'kind':kind},
                          fork_slug=lambda *a: pytest.fail('A source-only match looked up and synchronized its project'))
    net.project_store = lambda _: net.store
    worker = twins.Twins(net)
    proj = {'id':1, 'slug':'example'}
    builds = []
    def build(proj):
        builds.append(proj['id'])
        return {1:twins.Print('same-target', [], 4)}
    monkeypatch.setattr(worker, '_fork_prints', build)
    monkeypatch.setattr(worker, '_rom_prints', build)
    batches = []
    def pairs(proj, batch):
        batches.append(set(batch))
        assert worker.prints(proj)[1].key == 'same-target'
        return []
    monkeypatch.setattr(worker, 'pairs', pairs)
    worker.prints(proj)  # target fingerprints were already read before these source-only matches
    for fid in (1,2,3):worker.after_match(proj, fid)
    assert builds == [1]
    assert batches == [{1},{2},{3}]
    # Layout/target invalidations still force a refresh, and imported targets retain their bounded expiry.
    worker.forget(proj)
    worker.prints(proj)
    assert builds == [1,1]
    clock[0] += twins.FORK_PRINTS_TTL + 1
    worker.prints(proj)
    assert len(builds) == (3 if kind == 'fork' else 2)


def test_rom_index_does_not_sync_progress_while_holding_shared_index_lock(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('game', 'Example', 'mips', str(tmp_path), json.dumps(cfg)))
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', 'game')
    monkeypatch.setattr(net.twins, '_rom_prints', lambda _: {})
    finished, errors = threading.Event(), []
    def index():
        try:
            net.twins.index_project(proj)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=index, daemon=True)
    try:
        # A concurrent progress restore holds this while it verifies code. Its next successful
        # match also needs the shared index, so the index must not acquire this lock in reverse.
        with net.progress._project_lock('game'):
            worker.start()
            assert finished.wait(2), 'fingerprint indexing waited for project synchronization'
    finally:
        worker.join(timeout=3)
    assert not errors


def test_reuse_progress_is_published_without_a_manual_job_callback():
    from decompnet.app.cross_game_progress import report
    from decompnet.app.twins import Job

    job = Job()
    report(job, 'Checking identical code', function='current_function', done=3, total=12)
    assert job.progress['function'] == 'current_function'
    assert job.progress['done'] == 3 and job.progress['total'] == 12
    snapshots = []
    job.cross_report = snapshots.append
    report(job, 'Compiling candidate', function='next_function')
    assert snapshots[-1] == job.progress
    assert job.progress['done'] is None and job.progress['total'] is None


def test_local_reuse_pass_reports_active_function_and_counts_skipped_candidates(tmp_path, monkeypatch):
    from decompnet.app.twins import Job

    net = RomNetwork(tmp_path / 'reuse.sqlite', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('reuse', 'Example', 'mips', str(tmp_path), json.dumps(cfg))).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,status,size,n_insns,vram) VALUES(?,?,'open',4,1,4096)",
                         (pid, 'current_function')).lastrowid
    job = Job()
    snapshots = []
    job.cross_report = snapshots.append
    monkeypatch.setattr(net.twins, 'pairs', lambda *args: [(fid, fid)])
    monkeypatch.setattr(net.twins, 'known_miss', lambda *args: False)
    def trial(*args):
        assert job.progress['function'] == 'current_function'
        assert job.progress['done'] == 0 and job.progress['total'] == 1
        return 'skip'
    monkeypatch.setattr(net.twins, 'try_pair', trial)
    net.twins.run('reuse', job, workers=1)
    assert job.progress['done'] == job.progress['total'] == 1
    assert job.progress['function'] == ''
    assert job.tried == 0  # processed includes skipped candidates; compiled attempts stay separate
