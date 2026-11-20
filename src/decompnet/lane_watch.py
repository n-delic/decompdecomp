"""Where the window's request lanes are stuck: the code locations of the threads holding a lane.

The window's reads run in small worker lanes (app/server.py: ui, fast, live, ...). When both threads of the fast
lane waited on a lock for over ten minutes, opening a 2,000-function project showed "loading functions" for 49 s and
more while the same map took 0.07 s on a copy of its database, and the sidebar froze with it. The slow-request log
names a request only once it finishes, the project lock diagnostics only waits for project locks, and the event-loop
watchdog only a stalled loop, so nothing said which lock those threads waited for.

A Watch records each lane request while it queues for a worker and while it runs. When one has queued or run for
`threshold` seconds, it writes the lane's state to one bounded local report: every request in the lane with its
route and age, and the Python stack of each thread running one. Code locations only, like hang_diagnostics: never
frame locals, arguments, query strings, SQL or game data. One report per lane per `cooldown`, the latest few kept."""
from __future__ import annotations

import itertools
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

KEPT_BYTES = 512_000
STACK_DEPTH = 40


class Watch:
    def __init__(self, path, *, threshold=10., interval=1., cooldown=60., clock=time.monotonic):
        self.path = Path(path)
        self.threshold, self.interval, self.cooldown, self.clock = threshold, interval, cooldown, clock
        self.requests: dict[int, dict] = {}  # id -> {lane, route, queued, started, thread}
        self.ids = itertools.count()
        self.last_report: dict[str, float] = {}
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    @contextmanager
    def queued(self, lane: str, route: str):
        """Around the whole lane request, from before it waits for a worker until it ends."""
        # This runs on the event loop, which must never wait for a lock: one dict store and one pop are atomic
        # under the GIL, so the watcher thread's snapshot (dict.copy, also atomic) needs no lock either.
        key = next(self.ids)
        self.requests[key] = {'lane': lane, 'route': route or 'request', 'queued': self.clock(),
                              'started': None, 'thread': None}
        try:
            yield key
        finally:
            self.requests.pop(key, None)

    def running(self, key: int) -> None:
        """Called in the worker thread once the request has its worker."""
        if (entry := self.requests.get(key)) is not None:
            entry.update(started=self.clock(), thread=threading.get_ident())

    def check(self) -> list[str]:
        """Write a report for each lane with a request stuck past the threshold; returns those lanes."""
        now = self.clock()
        entries = [dict(e) for e in self.requests.copy().values()]
        stuck = sorted({e['lane'] for e in entries if now - e['queued'] >= self.threshold
                        and now - self.last_report.get(e['lane'], float('-inf')) >= self.cooldown})
        for lane in stuck:
            self.last_report[lane] = now
            self._report(lane, [e for e in entries if e['lane'] == lane], now)
        return stuck

    def _report(self, lane, entries, now):
        frames = sys._current_frames()
        names = {t.ident: t.name for t in threading.enumerate()}
        running = sorted((e for e in entries if e['thread'] is not None), key=lambda e: e['started'])
        waiting = sorted((e for e in entries if e['thread'] is None), key=lambda e: e['queued'])
        lines = [f'Request lane "{lane}" stuck; captured at {time.strftime("%Y-%m-%d %H:%M:%S %z")}\n',
                 f'{len(running)} running, {len(waiting)} waiting for a worker\n']
        for e in waiting:
            lines.append(f'  waiting {now - e["queued"]:7.1f}s  {e["route"]}\n')
        for e in running:
            lines.append(f'\nRunning {now - e["started"]:.1f}s (queued {e["started"] - e["queued"]:.1f}s) '
                         f'{e["route"]} on thread {e["thread"]} {names.get(e["thread"], "")}\n')
            frame = frames.get(e['thread'])
            stack = []
            while frame is not None and len(stack) < STACK_DEPTH:
                stack.append(f'  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n')
                frame = frame.f_back
            lines.extend(stack or ['  (thread finished)\n'])
        report = ''.join(lines) + '\n'
        try:
            previous = self.path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            previous = ''
        text = (previous + report)[-KEPT_BYTES:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(text, encoding='utf-8')
            os.replace(temp, self.path)
            import logging
            logging.getLogger('decompdecomp').warning(
                'Request lane %s stuck: %d running, %d waiting; thread locations saved to %s',
                lane, len(running), len(waiting), self.path)
        except OSError:
            pass  # a full or read-only disk must not break the lanes

    def _watch(self):
        while not self.stop.wait(self.interval):
            try:
                self.check()
            except Exception:  # noqa: BLE001 (diagnostics never take the server down)
                pass

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._watch, daemon=True, name='request-lane-watch')
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=.2)
