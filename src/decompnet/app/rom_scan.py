"""Run ROM Explorer analysis in a fresh process and forward its job progress."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .. import limits
from ..hostenv import NO_WINDOW
from ..network import NetworkError

WORKER_SOURCE = Path(__file__).with_name("rom_scan_worker.py")
TIMEOUT = 30 * 60


def _messages(stream, events):
    try:
        for line in stream:
            events.put(line)
    finally:
        events.put(None)


def run(source: bytes, request: dict, log):
    """Use the exact worker source revision selected for this scan, even during a Git update."""
    log.check_cancel()
    with tempfile.TemporaryDirectory(prefix="decomp-rom-scan-") as folder:
        work = Path(folder)
        script, inputs, report = (work / name for name in ("scan.py", "inputs.json", "report.json"))
        script.write_bytes(source)
        with inputs.open("w", encoding="utf-8") as stream:
            json.dump(request, stream)
        argv = limits.capped([sys.executable, str(script), str(inputs), str(report)])
        with tempfile.TemporaryFile() as diagnostics, subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics,
            start_new_session=os.name != "nt", creationflags=NO_WINDOW,
        ) as process:
            limits.attach_job(process)
            events = queue.SimpleQueue()
            reader = threading.Thread(target=_messages, args=(process.stdout, events), daemon=True, name="rom-scan-events")
            reader.start()
            try:
                with limits.CompilerMemoryWatchdog(process, limit=limits.default_cap(), timeout=TIMEOUT,
                                                   whole_tree=True) as watchdog:
                    while True:
                        log.check_cancel()
                        try:
                            line = events.get(timeout=.1)
                        except queue.Empty:
                            continue
                        if line is None:
                            break
                        event = json.loads(line)
                        if "log" in event:
                            log(event["log"])
                        elif "progress" in event:
                            log.progress(event["progress"])
                        elif "error" in event:
                            raise NetworkError(event["error"])
                    while process.poll() is None:
                        log.check_cancel()
                        try:
                            process.wait(timeout=.1)
                        except subprocess.TimeoutExpired:
                            pass
            finally:
                if process.poll() is None:
                    limits.stop_group(process)
                    if process.poll() is None:
                        process.kill()
                limits.release_job(process)
                reader.join(timeout=1)
            log.check_cancel()
            if watchdog.timed_out:
                raise NetworkError("The ROM scan exceeded 30 minutes and was stopped. Try again or report the problem.")
            if watchdog.exceeded:
                raise NetworkError("The ROM scan exceeded its memory limit. Close other work and try again.")
            if process.returncode or not report.is_file():
                diagnostics.seek(max(0, diagnostics.seek(0, os.SEEK_END) - 4000))
                if detail := diagnostics.read().decode("utf-8", errors="replace").strip():
                    log(detail)
                raise NetworkError("The ROM scan worker stopped before returning a map. Try scanning again or report the job log.")
        with report.open(encoding="utf-8") as stream:
            return json.load(stream)
