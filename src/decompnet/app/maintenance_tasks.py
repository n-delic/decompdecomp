"""Background passes run in the permuter worker's process instead of the app's.

The app is one Python process whose threads share an interpreter lock with the event loop, the window's requests
and the agents' tool calls. The thread CPU report (thread_cpu.py) of a live app put its automatic passes at about
55% of the app's processor time: the twins full pass (another release's code, matched-source reuse, similar
functions, near-match repairs) and the CPU repair handoff after an agent releases a function. They read the
databases, compile and record attempts through the ordinary judge, and need nothing the app keeps in memory, so the
permuter worker (permuter_worker.py), which already owns a whole engine on the same databases, runs them.

The app keeps what the window and the agents see: the twins Job (its phase, counts, Stop and the user's "do it
now" promotion) and the job log of a repair handoff. The worker sends their progress once a second and the app
copies it onto them. When no worker can answer (DECOMP_MAINTENANCE_WORKER=0, a coordination engine without one, a
code update pausing it) the pass runs in the app as it always did.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from ..network import NetworkError

log = logging.getLogger("decompdecomp")
STATUS_SECONDS = 1.0


class Unavailable(Exception):
    """No worker can run the task: run it in the app instead."""


def enabled() -> bool:
    return os.environ.get("DECOMP_MAINTENANCE_WORKER", "").strip() != "0"


def worker_of(net):
    """The app's permuter worker, when background passes may run in it."""
    from .permuter_worker import PermuterWorker
    worker = getattr(net, "permuter", None)
    return worker if enabled() and isinstance(worker, PermuterWorker) else None


def job_fields(job) -> dict:
    """A job's plain fields (what the window reads), without its events, callbacks or anything not JSON."""
    fields = {}
    for name, value in vars(job).items():
        if name.startswith("_") or isinstance(value, threading.Event) or callable(value):
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        fields[name] = value
    return fields


def apply_fields(job, fields: dict) -> None:
    """Copy a worker's snapshot onto the app's job; the app's own events stay as they are."""
    previous = getattr(job, "progress", None)
    for name, value in fields.items():
        if not isinstance(getattr(job, name, None), threading.Event):
            setattr(job, name, value)
    if (callback := getattr(job, "cross_report", None)) and fields.get("progress") not in (None, previous):
        try:
            callback(fields["progress"])
        except Exception:
            log.exception("A cross-game progress callback failed")


# ------------------------------------------------------------------------------------------------- the app's side
def full_pass(twins, slug: str, job):
    """Twins._execute_full_pass in the worker, its progress mirrored onto `job`; Unavailable to run it here."""
    worker = worker_of(twins.net)
    if worker is None:
        raise Unavailable("no worker")
    from .cross_game_progress import report
    try:
        twins.order(slug, job)  # (seconds, this project only: starting agents wait for it, so it stays here)
    except Exception:
        job.ordered.set()
        raise
    report(job, "Preparing reusable-code search")
    try:
        result = worker.run_task("full_pass", {"slug": slug, "foreground": job.foreground.is_set()},
                                 stop=job.stop, promote=job.foreground,
                                 on_status=lambda fields: apply_fields(job, fields))
        apply_fields(job, result)
    finally:
        job.ordered.set()
        if job.state == "running":
            job.state = "stopped" if job.stop.is_set() else "done"
        twins.net.cascades.wake()
    return job


def deep_run(net, slug: str, name: str, minutes, variants, log_, *, maximum_seconds=None, parallel_claims=None):
    """deep_permuter.run in the worker, its lines and progress written to `log_`; Unavailable to run it here."""
    worker = worker_of(net)
    if worker is None:
        raise Unavailable("no worker")
    stop = getattr(log_, "cancel_event", None) or threading.Event()

    def status(fields):
        if "line" in fields:
            log_(fields["line"])
        if "progress" in fields and hasattr(log_, "progress"):
            log_.progress(fields["progress"])

    payload = {"slug": slug, "name": name, "minutes": minutes, "variants": variants,
               "maximum_seconds": maximum_seconds, "parallel_claims": parallel_claims}
    watcher = None
    if not hasattr(log_, "cancel_event") and hasattr(log_, "check_cancel"):
        # A job log without an event: notice its Stop by asking it, as deep_permuter would.
        def watch():
            while not stop.is_set():
                try:
                    log_.check_cancel()
                except Exception:
                    stop.set()
                    return
                time.sleep(.2)
        watcher = threading.Thread(target=watch, daemon=True, name="repair-handoff-cancel")
        watcher.start()
    try:
        result = worker.run_task("deep_run", payload, stop=stop, on_status=status)
    finally:
        if watcher is not None:
            stop.set()
    if hasattr(log_, "check_cancel"):
        log_.check_cancel()  # (stopped: say so as the in-app run would)
    return result


# ---------------------------------------------------------------------------------------------- the worker's side
def _ticker(stop: threading.Event, done: threading.Event, send):
    def tick():
        while not done.wait(STATUS_SECONDS):
            send()
    thread = threading.Thread(target=tick, daemon=True, name="maintenance-task-status")
    thread.start()
    return thread


def _run_full_pass(net, payload, stop, promote, status):
    from . import maintenance
    from .twins import Job
    job = Job()
    job.stop = stop
    job.foreground = promote
    job.ordered.set()  # (the app ordered the claims before asking)
    if payload.get("foreground"):
        job.promote()
    done = threading.Event()
    _ticker(stop, done, lambda: status(job_fields(job)))
    try:
        with maintenance.foreground(job.foreground), maintenance.slot(stop) as ready:
            if ready:
                net.twins._execute_full_pass(payload["slug"], job)
            else:
                job.state = "stopped"
    finally:
        done.set()
    return job_fields(job)


class _TaskLog:
    """What deep_permuter.run needs of a job log, sending its lines and progress to the app."""

    def __init__(self, stop, status, maximum_seconds, parallel_claims):
        self.cancel_event = stop
        self._status = status
        if maximum_seconds is not None:
            self.maximum_seconds = maximum_seconds
        if parallel_claims is not None:
            self.parallel_claims = parallel_claims

    def __call__(self, line):
        self.check_cancel()
        self._status({"line": str(line)})

    def progress(self, value):
        self.check_cancel()
        self._status({"progress": dict(value)})

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise RuntimeError("Cancelled")


def _run_deep(net, payload, stop, promote, status):
    from . import deep_permuter
    task_log = _TaskLog(stop, status, payload.get("maximum_seconds"), payload.get("parallel_claims"))
    result = deep_permuter.run(net, payload["slug"], payload["name"], payload["minutes"], payload["variants"], task_log)
    return json.loads(json.dumps(result, default=str))


TASKS = {"full_pass": _run_full_pass, "deep_run": _run_deep}


def run(net, kind, payload, stop, promote, status) -> dict:
    """Run one task in the worker: {"result": ...}, or {"error": message, "type": the error's kind}."""
    try:
        return {"result": TASKS[kind](net, payload, stop, promote, status)}
    except NetworkError as error:
        return {"error": str(error), "type": "NetworkError"}
    except Exception as error:
        if stop.is_set():
            return {"error": "Cancelled", "type": "Cancelled"}
        log.exception("maintenance task %s failed in the worker", kind)
        return {"error": f"{type(error).__name__}: {error}", "type": "Error"}
