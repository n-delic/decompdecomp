"""A real child owns permutation work while the app engine remains responsive."""

import threading
import time

import pytest

from decompnet.app import permuter_worker, workers
from decompnet.app.engine import RomNetwork
from decompnet.app.permuter_worker import PermuterWorker
from tests.test_loading import tiny_game


@pytest.fixture(autouse=True)
def restore_parent_worker_configuration():
    previous = workers.WORKERS
    yield
    workers.configure(previous)


def wait_for(check, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.05)
    raise AssertionError("the permuter worker did not reach the expected state")


def test_formatting_code_update_changes_worker_revision(tmp_path, monkeypatch):
    """permuter_formatting.py moved to decomprepair (0.3.0): the worker watches the library's file."""
    (tmp_path/'lib').mkdir()
    (tmp_path/'lib'/'permuter_formatting.py').write_text('formatting code')
    monkeypatch.setattr(permuter_worker, '__file__', str(tmp_path/'app'/'permuter_worker.py'))
    monkeypatch.setattr(permuter_worker, '_library', lambda: tmp_path/'lib')
    before = permuter_worker.revision()
    (tmp_path/'lib'/'permuter_formatting.py').write_text('updated formatting code, longer')
    assert permuter_worker.revision() != before


@pytest.mark.parametrize('module', ['argument_mask_repairs.py', 'inline_scheduling_repairs.py', 'loop_weight_repairs.py'])
def test_argument_capture_update_changes_worker_revision(tmp_path, monkeypatch, module):
    """These families live in decomprepair: an update to the library's file restarts the worker."""
    (tmp_path/'lib').mkdir()
    (tmp_path/'lib'/module).write_text('argument capture')
    monkeypatch.setattr(permuter_worker, '__file__', str(tmp_path/'app'/'permuter_worker.py'))
    monkeypatch.setattr(permuter_worker, '_library', lambda: tmp_path/'lib')
    before = permuter_worker.revision()
    (tmp_path/'lib'/module).write_text('updated argument capture')
    assert permuter_worker.revision() != before


def test_app_owned_code_update_changes_worker_revision(tmp_path, monkeypatch):
    (tmp_path/'app').mkdir()
    monkeypatch.setattr(permuter_worker, '__file__', str(tmp_path/'app'/'permuter_worker.py'))
    monkeypatch.setattr(permuter_worker, '_library', lambda: tmp_path/'lib')
    before = permuter_worker.revision()
    (tmp_path/'app'/'permute.py').write_text('updated permuter')
    assert permuter_worker.revision() != before


def test_permuter_restarts_without_replacing_the_app_engine(tmp_path, monkeypatch):
    internal = tmp_path / ".decomp"
    internal.mkdir()
    net = RomNetwork(internal / "decomp.db", tmp_path)
    tiny_game(net, functions=0)
    previous_workers=workers.WORKERS
    worker = net.permuter = PermuterWorker(net)
    try:
        worker.configure({"permuter_seconds": 9, "worker_threads": 1})
        worker.start("game")
        wait_for(lambda: worker.status("game") and "threads" in worker.status("game"))
        first = worker._process.pid
        assert net.project_row("game")["slug"] == "game"
        assert worker.repair_pass("game", threading.Event()).matched == 0

        original = worker._revision
        monkeypatch.setattr(permuter_worker, "revision", lambda: original + (("edited", 1, 1),))
        wait_for(lambda: worker._process and worker._process.pid != first and
                 worker.status("game") and "threads" in worker.status("game"))
        assert net.project_row("game")["slug"] == "game"

        worker._process.kill()
        wait_for(lambda: worker.status("game")["state"] == "stopped")
        assert net.project_row("game")["slug"] == "game"
    finally:
        worker.close()
        workers.configure(previous_workers)



def test_worker_diagnostics_have_timestamp_pid_revision_and_keep_stdout_json(tmp_path, monkeypatch):
    import json
    import re
    import subprocess
    import sys

    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setenv('DECOMP_HOME',str(tmp_path/'data'))
    script = ("import json,sys; from decompnet.app.permuter_worker import _worker_logging,log; "
              "_worker_logging(); log.warning('Synthetic worker warning'); "
              "sys.stdout.write(json.dumps({'kind':'status','jobs':{}})); "
              "sys.excepthook(ValueError,ValueError('Synthetic worker failure'),None); "
              "sys.excepthook(SystemExit,SystemExit('Synthetic normal exit'),None)")
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=10,check=True)
    assert json.loads(result.stdout)=={'kind':'status','jobs':{}}
    assert len(result.stdout.splitlines())==1
    assert re.search(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING .*pid=\d+',result.stderr)
    assert 'Permuter worker starting:' in result.stderr and 'RetroReversing fork' in result.stderr
    assert 'Uncaught permuter worker error' in result.stderr
    assert 'Synthetic worker failure' in result.stderr
    assert 'Synthetic normal exit' not in result.stderr


@pytest.mark.parametrize('module', ['read_cache.py','metadata_rows.py','function_rows.py','candidate_scope.py','claim_stalls.py','match_bundle.py','network.py','compiler.py','compiler_processes.py','saved_compilation.py','app/engine/judging.py','app/wrapper_families.py','app/view_snapshots.py','app/code_coverage.py','app/progress.py','app/saves.py'])
def test_shared_contract_update_changes_worker_revision(tmp_path, monkeypatch, module):
    root=tmp_path/'decompnet'
    file=root/module
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text('old')
    monkeypatch.setattr(permuter_worker,'__file__',str(root/'app/permuter_worker.py'))
    before=permuter_worker.revision()
    file.write_text('updated shared contract')
    assert permuter_worker.revision()!=before


@pytest.mark.parametrize('action', ['start', 'repair_pass'])
def test_child_shutdown_waits_for_owned_compiler_workspace_cleanup(tmp_path, monkeypatch, action):
    import io
    import json
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from decompnet.app import engine, permute
    from decompnet.compiler import _workspace

    started, stopping, release, returned = (threading.Event() for _ in range(4))
    monkeypatch.setenv('DECOMP_COMPILER_TMPDIR', str(tmp_path/'compiler'))
    monkeypatch.setattr(engine, 'RomNetwork', lambda *a: SimpleNamespace(links={}))
    monkeypatch.setattr(permuter_worker, '_worker_logging', lambda: None)
    paths = []
    def work(stop):
        with _workspace('synthetic-worker-') as folder:
            from pathlib import Path
            paths.append(Path(folder))
            (paths[-1]/'compiler-output').write_text('synthetic')
            started.set()
            assert stop.wait(3)
            stopping.set()
            assert release.wait(3)
    def loop(self, slug, state):
        work(state.stop)
        state.state = 'stopped'
    def repair(self, slug, stop):
        work(stop)
        return permute.State(stop=stop)
    monkeypatch.setattr(permute.Permuter, '_loop', loop)
    monkeypatch.setattr(permute.Permuter, 'repair_pass', repair)
    def messages():
        yield json.dumps(dict(action=action, slug='game', ident=1))+'\n'
        assert started.wait(3)
        yield '{"action":"shutdown"}\n'
    monkeypatch.setattr(permuter_worker.sys, 'stdin', messages())
    monkeypatch.setattr(permuter_worker.sys, 'stdout', io.StringIO())
    def child():
        try:permuter_worker.child('unused.db', str(tmp_path))
        finally:returned.set()
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(child)
        try:
            assert stopping.wait(3)
            assert not returned.wait(.1), 'Child returned while its compiler workspace was still in use'
        finally:release.set()
        pending.result(timeout=3)
    assert paths and not paths[0].exists()
