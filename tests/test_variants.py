"""A visible game never merges target state, and shared edits require independent byte proof."""
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import variants
from decompnet.network import NetworkError
from decompnet.store import Store


@pytest.fixture
def net(tmp_path):
    store = Store(tmp_path / 'catalog.db')
    with store.tx() as db:
        for pid, slug in enumerate(('pal', 'us', 'jp'), 1):
            cfg = json.dumps({'kind': 'rom', 'platform': 'n64'})
            db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(?,?,?,?,?)', (pid, slug, slug, str(tmp_path / slug), cfg))
            db.execute('INSERT INTO functions(id,project_id,name,target,status,best_source,matched_source,unit) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, pid, 'update', b'\0\1\2\3', 'matched', 'old C', 'old C', ''))
    class Net:
        links = {}
        def project_row(self, slug):
            return store.one('SELECT * FROM projects WHERE slug=?', slug)
        def project_store(self, _):return store
        def cfg(self, proj):return json.loads(proj['config'])
        def is_rom(self, proj):return self.cfg(proj).get('kind') == 'rom'
        def fork_slug(self, _):return False
        def _context(self, *_):return {'header': 'target header'}
        def _compile(self, proj, header, source, fn):
            self.compiled.append((proj['slug'], header, source, fn['id']))
            return SimpleNamespace(ok=True, obj=source.encode(), log='')
        def _evaluate(self, fn, proj, obj):return obj, proj['slug'] not in self.fail, ['changed bytes']
    result = Net()
    result.store, result.compiled, result.fail = store, [], set()
    yield result
    store.close_readers()
    store.conn.close()


def attached(net):
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    return variants.bind(net, 'pal', 'update', [{'variant':'pal','function_id':1}, {'variant':'us','function_id':2}])


def test_attaching_changes_no_work_or_history_and_sidebar_has_one_game(net):
    before = [dict(r) for r in net.store.all('SELECT * FROM functions')]
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    assert [dict(r) for r in net.store.all('SELECT * FROM functions')] == before
    rows = [{'slug':'pal','title':'pal','working':1}, {'slug':'us','title':'us','working':2}, {'slug':'jp','title':'jp'}]
    visible = variants.visible(net, rows)
    assert [r['slug'] for r in visible] == ['pal','jp']
    assert visible[0]['working'] == 3
    variants.detach(net, 'pal', 'us')
    assert len(variants.visible(net, rows)) == 3
    assert net.store.one('SELECT matched_source FROM functions WHERE id=2')['matched_source'] == 'old C'


def test_membership_is_atomic_and_variant_ids_cannot_escape_export(net):
    for variant in ('../us', '/tmp', 'US', 'a/b', ''):
        with pytest.raises(NetworkError):variants.attach(net, 'pal', 'us', variant)
    assert not variants.member(net, 'pal')
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    with pytest.raises(NetworkError):variants.attach(net, 'jp', 'us', 'us')
    with pytest.raises(NetworkError):variants.attach(net, 'pal', 'jp', 'us')
    assert variants.member(net, 'jp') is None


def test_logical_identity_supports_split_merged_missing_and_inlined_instances(net):
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    variants.attach(net, 'pal', 'jp', 'jp')
    logical = variants.bind(net, 'pal', 'operation', [
        {'variant':'pal','function_id':1,'relation':'split'},
        {'variant':'us','relation':'absent'}, {'variant':'jp','relation':'inlined'}])
    other = variants.bind(net, 'pal', 'another operation', [{'variant':'pal','function_id':1,'relation':'merged'}])
    assert logical != other
    assert len(net.store.all('SELECT * FROM variant_bindings WHERE logical=?', logical)) == 3
    # IDs survive target symbol changes; a function can implement more than one logical operation.
    with net.store.tx() as db:db.execute('UPDATE functions SET name=? WHERE id=1', ('renamed',))
    assert net.store.one('SELECT function_id FROM variant_bindings WHERE logical=? AND variant=?', logical, 'pal')['function_id'] == 1
    with pytest.raises(NetworkError):variants.bind(net, 'pal', 'wrong target', [{'variant':'us','function_id':1}])


def test_a_failed_shared_edit_preserves_every_existing_match_and_revision(net):
    logical = attached(net)
    before = [dict(r) for r in net.store.all('SELECT * FROM functions')]
    first = variants.publish(net, 'pal', logical, 'good C', 'shared header')
    assert first['verified'] == 2
    assert {r[0] for r in net.compiled} == {'pal','us'}
    net.fail = {'us'}
    with pytest.raises(NetworkError, match='did not match exactly'):
        variants.publish(net, 'us', logical, 'bad C', expected=first['revision'])
    assert net.store.one('SELECT COUNT(*) AS n FROM variant_sources')['n'] == 1
    assert [dict(r) for r in net.store.all('SELECT * FROM functions')] == before
    assert not net.store.all('SELECT * FROM variant_edit_locks')
    assert variants.shared_sources(net, 'us')[2]['revision'] == first['revision']


def test_shared_edits_reject_stale_writers_and_concurrent_editors(net):
    logical = attached(net)
    variants.publish(net, 'pal', logical, 'first')
    with pytest.raises(NetworkError, match='changed'):
        variants.publish(net, 'pal', logical, 'stale', expected=None)
    with variants.edit_lock(net, variants.member(net, 'pal')['game'], 'agent-one'):
        with pytest.raises(NetworkError, match='Another shared-source edit'):
            variants.publish(net, 'us', logical, 'conflict')


def test_conditionals_and_separate_implementations_are_judged_per_target(net):
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    logical = variants.bind(net, 'pal', 'operation', [
        {'variant':'pal','function_id':1,'defines':{'REGION':1}},
        {'variant':'us','function_id':2,'defines':{'REGION':2},'override':'separate implementation'}])
    variants.publish(net, 'pal', logical, '#if REGION == 1\nfirst\n#endif', 'shared declarations')
    assert '#define REGION 1' in net.compiled[0][2]
    assert 'separate implementation' in net.compiled[1][2]
    assert '#define REGION 2' in net.compiled[1][2]
    assert all(row[1] == 'target header' for row in net.compiled)


def test_stop_before_publication_saves_no_partial_shared_revision(net):
    logical = attached(net)
    class Log:
        count = 0
        def __call__(self, _):self.count += 1
        def check_cancel(self):
            if self.count:raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError, match='Stopped'):
        variants.publish(net, 'pal', logical, 'C', log=Log())
    assert not net.store.all('SELECT * FROM variant_sources')
    assert not net.store.all('SELECT * FROM variant_edit_locks')


def test_jobs_persist_progress_and_reattach_without_starting_twice(net, tmp_path):
    from decompnet.app.support import Jobs
    attached(net)
    jobs, entered, finish = Jobs(), threading.Event(), threading.Event()
    def work(log):
        log.progress({'done':1,'total':2})
        entered.set()
        assert finish.wait(5)
        return {'verified':2}
    jid = variants.start_job(net, jobs, 'pal', 'shared-test', work)
    assert entered.wait(5)
    assert variants.start_job(net, jobs, 'us', 'shared-test', work) == jid
    snapshot = variants.job_status(net, jobs, 'us')[0]
    assert snapshot['runtime_id'] == jid and snapshot['progress']['done'] == 1
    finish.set()
    for _ in range(100):
        if jobs.jobs[jid]['state'] != 'running':break
        threading.Event().wait(.01)
    assert variants.job_status(net, jobs, 'pal')[0]['result'] == {'verified':2}
    with net.store.tx() as db:db.execute("UPDATE variant_jobs SET state='running'")
    assert variants.job_status(net, Jobs(), 'pal')[0]['state'] == 'interrupted'


def test_export_dispatcher_builds_all_targets_even_when_one_fails_and_isolates_caches(tmp_path):
    from shutil import copyfile

    from decompnet.rom import export_variant_build
    copyfile(Path(export_variant_build.__file__), tmp_path / 'build.py')
    manifest = {}
    for variant, code in [('pal', 1), ('us', 0)]:
        folder = tmp_path / 'variants' / variant
        folder.mkdir(parents=True)
        (folder / 'build.py').write_text(f'from pathlib import Path\nPath("build").mkdir(exist_ok=True)\nPath("build/ran").write_text("{variant}")\nraise SystemExit({code})\n')
        manifest[variant] = {'path': f'variants/{variant}'}
    (tmp_path / 'variants.json').write_text(json.dumps({'variants': manifest}))
    result = subprocess.run([sys.executable, str(tmp_path/'build.py'), '--all-variants'], capture_output=True)
    assert result.returncode == 1
    for variant in manifest:assert (tmp_path/'variants'/variant/'build/ran').read_text() == variant
    assert subprocess.run([sys.executable, str(tmp_path/'build.py'), '--variant', 'us'], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, str(tmp_path/'build.py'), '--variant', '../pal'], capture_output=True).returncode != 0


def test_stop_interrupts_a_silent_export_process(tmp_path):
    from decompnet.rom.export_process import run
    class Log:
        checks = 0
        def check_cancel(self):
            self.checks += 1
            if self.checks > 1:raise RuntimeError('Stopped')
    import time
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='Stopped'):
        run([sys.executable, '-c', 'import time;time.sleep(30)'], tmp_path, Log())
    assert time.monotonic() - start < 5


def test_reimported_function_id_cannot_silently_reuse_a_shared_identity(net):
    logical = attached(net)
    variants.publish(net, 'pal', logical, 'C')
    with net.store.tx() as db:db.execute('UPDATE functions SET target=? WHERE id=2', (b'replaced',))
    with pytest.raises(NetworkError, match='replaced or removed'):
        variants.shared_sources(net, 'us')


def test_membership_cannot_change_during_shared_verification(net):
    attached(net)
    own = variants.member(net, 'pal')
    with variants.edit_lock(net, own['game'], 'export'):
        with pytest.raises(NetworkError, match='Wait before changing'):
            variants.attach(net, 'pal', 'jp', 'jp')


def test_overlapping_function_ids_in_project_databases_stay_variant_scoped(net, tmp_path):
    other = Store(tmp_path / 'other.db')
    with other.tx() as db:
        db.execute("INSERT INTO functions(id,project_id,name,target,status,unit) VALUES(1,2,'update',?,'open','')", (b'other bytes',))
    net.project_store = lambda pid: other if pid == 2 else net.store
    variants.attach(net, 'pal', 'us', 'us', 'pal')
    logical = variants.bind(net, 'pal', 'operation', [{'variant':'pal','function_id':1},{'variant':'us','function_id':1}])
    assert variants.publish(net, 'us', logical, 'C')['verified'] == 2
    assert len(variants.shared_sources(net, 'pal')) == len(variants.shared_sources(net, 'us')) == 1
    other.close_readers()
    other.conn.close()


def test_cancelled_job_admission_does_not_reattach_forever(net):
    attached(net)
    own = variants.member(net, 'pal')
    with net.store.tx() as db:
        db.execute('INSERT INTO variant_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                   ('durable',own['game'],'pal','running','{}',None,None,1,'cancelled-before-worker'))
    jobs = SimpleNamespace(jobs={'cancelled-before-worker':{'state':'cancelled','error':'Stopped before verification'}})
    assert variants.job_status(net, jobs, 'pal')[0]['state'] == 'cancelled'
