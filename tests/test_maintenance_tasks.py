"""Background passes run in the permuter worker's process, their progress shown on the app's own job.

The thread CPU report of a live app put the twins full pass and the CPU repair handoff at about half the app's
processor time, competing with the window and the agents' tool calls for one interpreter lock."""
import io
import json
import threading
import time

import pytest

from decompnet.app import maintenance_tasks, permuter_worker, twins, workers
from decompnet.app.engine import RomNetwork
from decompnet.app.permuter_worker import PermuterWorker
from decompnet.network import NetworkError
from tests.test_loading import tiny_game


@pytest.fixture(autouse=True)
def restore_parent_worker_configuration():
    previous = workers.WORKERS
    yield
    workers.configure(previous)


@pytest.fixture
def app(tmp_path):
    internal = tmp_path / ".decomp"
    internal.mkdir()
    net = RomNetwork(internal / "decomp.db", tmp_path)
    tiny_game(net, functions=0)
    worker = net.permuter = PermuterWorker(net)
    worker.configure({"worker_threads": 1})
    try:
        yield net, worker
    finally:
        worker.close()


def test_a_full_pass_runs_in_the_worker_and_finishes_the_apps_job(app, monkeypatch):
    net, worker = app
    monkeypatch.setattr(twins.Twins, "_execute_full_pass",
                        lambda *args: pytest.fail("the full pass ran in the app's own process"))
    job = net.twins.full_pass("game")
    assert job.state == "done" and job.ordered.is_set()
    assert worker._process is not None and worker._process.poll() is None  # (the child ran it)
    assert job.progress.get("phase")  # its progress came back


def test_a_deep_runs_error_comes_back_as_the_apps_own(app):
    net, _ = app

    class Log:
        def __call__(self, line):
            pass

        def progress(self, value):
            pass

        def check_cancel(self):
            pass
    with pytest.raises(NetworkError, match="no_such_function|not found|no function|isn't"):
        maintenance_tasks.deep_run(net, "game", "no_such_function", 1, 1, Log())


def test_without_a_worker_the_pass_runs_in_the_app(app, monkeypatch):
    net, _ = app
    monkeypatch.setenv("DECOMP_MAINTENANCE_WORKER", "0")
    ran = []

    def here(self, slug, job):
        ran.append(slug)
        job.state = "done"
        job.ordered.set()
        return job
    monkeypatch.setattr(twins.Twins, "_execute_full_pass", here)
    assert net.twins.full_pass("game").state == "done"
    assert ran == ["game"]


def test_the_childs_task_sends_progress_honours_promotion_and_stops(tmp_path, monkeypatch):
    """The child's side of the protocol: status snapshots while it runs, the app's promotion, and Stop."""
    from types import SimpleNamespace

    from decompnet.app import engine
    started, promoted = threading.Event(), threading.Event()

    def slow(net, payload, stop, promote, status):
        status({"progress": {"phase": "working"}})
        started.set()
        assert promote.wait(3)
        promoted.set()
        assert stop.wait(3)
        raise RuntimeError("stopped")
    monkeypatch.setitem(maintenance_tasks.TASKS, "slow", slow)
    monkeypatch.setattr(engine, "RomNetwork", lambda *a: SimpleNamespace(links={}))
    monkeypatch.setattr(permuter_worker, "_worker_logging", lambda: None)

    def messages():
        yield json.dumps({"action": "task", "ident": 7, "kind": "slow", "payload": {}}) + "\n"
        assert started.wait(3)
        yield json.dumps({"action": "promote", "ident": 7}) + "\n"
        assert promoted.wait(3)
        yield json.dumps({"action": "cancel", "ident": 7}) + "\n"
        until = time.monotonic() + 3
        while '"reply"' not in out.getvalue() and time.monotonic() < until:
            time.sleep(.02)
        yield '{"action":"shutdown"}\n'
    out = io.StringIO()
    monkeypatch.setattr(permuter_worker.sys, "stdin", messages())
    monkeypatch.setattr(permuter_worker.sys, "stdout", out)
    permuter_worker.child("unused.db", str(tmp_path))
    sent = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {"kind": "task_status", "ident": 7, "fields": {"progress": {"phase": "working"}}} in sent
    assert {"kind": "reply", "ident": 7, "error": "Cancelled", "type": "Cancelled"} in sent


def test_mirroring_keeps_the_apps_events_and_tells_the_window():
    job = twins.Job()
    seen = []
    job.cross_report = seen.append
    stop, foreground = job.stop, job.foreground
    maintenance_tasks.apply_fields(job, {"progress": {"phase": "Trying the other release's code"}, "matched": 3,
                                         "cross_tried": 12, "stop": "not an event"})
    assert job.matched == 3 and job.cross_tried == 12
    assert job.stop is stop and job.foreground is foreground
    assert seen == [{"phase": "Trying the other release's code"}]
    fields = maintenance_tasks.job_fields(job)
    assert "stop" not in fields and "cross_report" not in fields and fields["matched"] == 3
