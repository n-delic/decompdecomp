"""Bounded, single-flight caches for derived read-only results; callers supply input revisions."""
import threading
import time
from collections import OrderedDict


class ReadCache:
    def __init__(self, bypass=lambda: False):
        self.bypass = bypass
        self.lock = threading.RLock()
        self.entries = OrderedDict()

    def _trim(self):
        # Never evict an in-flight entry: another reader must join its existing build.
        while len(self.entries) > 128:
            for oldest, entry in self.entries.items():
                if not entry['users']:
                    break
            else:
                return
            self.entries.pop(oldest)

    def get(self, key, version, build, ttl=None):
        if self.bypass():
            return build()
        with self.lock:
            entry = self.entries.setdefault(key, {'lock': threading.RLock(), 'users': 0})
            entry['users'] += 1
            self.entries.move_to_end(key)
            self._trim()
        try:
            with entry['lock']:
                if ('value' not in entry or entry['version'] != version or
                        (ttl is not None and time.monotonic() >= entry['expires'])):
                    value = build()  # failures never replace the last successful result
                    entry.update(version=version, value=value, expires=time.monotonic() + (ttl or 0))
                return entry['value']
        finally:
            with self.lock:
                entry['users'] -= 1
                self._trim()
