"""Bounded, function-attributed activity, coalesced across concurrent searches."""
import threading
import time

PUBLISH_SECONDS = 0.2


class Activity:
    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.active = {}
        self.recent = []
        self.summary = {}
        self.started = {}
        self.last_publish = float('-inf')
        self.timer = None

    def update(self, name, value=None, line=None, finished=False):
        with self.lock:
            fresh = name not in self.active
            self.started.setdefault(name, time.monotonic())
            self.recent = [w for w in self.recent if w['function'] != name]
            item = self.active.setdefault(name, {'function': name, 'phase': 'starting', 'tested': 0,
                                                 'improvements': 0, 'lines': []})
            urgent = fresh or finished or bool(value and (
                value.get('improvements', 0) > item.get('improvements', 0) or
                value.get('phase', item.get('phase')) != item.get('phase') or value.get('error')))
            if value:
                item.update(value)
            if line is not None:
                item['lines'] = [*item['lines'], str(line)[-800:]][-6:]
            item['updated_at'] = time.time()
            if finished:
                item['elapsed_seconds'] = max(0, time.monotonic() - self.started.pop(name))
                self.recent = [dict(item), *self.recent][:8]
                self.active.pop(name, None)
            try:
                self._schedule(urgent)
            except RuntimeError:
                if not finished:
                    raise

    def publish(self, value, *, force=True):
        with self.lock:
            urgent = force or value.get('matches', 0) > self.summary.get('matches', 0)
            self.summary.update(value)
            self._schedule(urgent)

    def _schedule(self, force):
        if force or time.monotonic() - self.last_publish >= PUBLISH_SECONDS:
            self._publish()
        elif self.timer is None:
            timer = threading.Timer(PUBLISH_SECONDS, self._flush)
            timer.daemon = True
            self.timer = timer
            timer.start()

    def _flush(self):
        with self.lock:
            # A cancelled timer can already be waiting on this lock; don't flush a newer timer.
            if self.timer is not threading.current_thread():
                return
            self.timer = None
            try:
                self._publish()
            except RuntimeError:
                pass  # A stopped job no longer accepts progress. Its complete log is retained.

    def _publish(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.last_publish = time.monotonic()
        self.log.progress({**self.summary, 'workers': [dict(w, elapsed_seconds=max(0, time.monotonic() - self.started[name]))
                                                     for name, w in self.active.items()],
                           'recent_workers': list(self.recent)})
