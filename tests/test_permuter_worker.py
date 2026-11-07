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
    monkeypatch.setattr(permuter_worker, '__file__', str(tmp_path/'app'/'permuter_worker.py'))
    before = permuter_worker.revision()
    (tmp_path/'permuter_formatting.py').write_text('updated formatting code')
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
