"""Bounded, single-flight caches for derived read-only results; callers supply input revisions."""
import threading
import time


class ReadCache:
    def __init__(self, bypass=lambda: False):
        self.bypass = bypass
        self.lock = threading.RLock()
        self.entries = {}

    def get(self, key, version, build, ttl=None):
        if self.bypass():
            return build()
        # Use stable keys, not versions as keys, so changing inputs cannot grow the cache.
        with self.lock:
            entry = self.entries.setdefault(key, {'lock': threading.RLock()})
            if len(self.entries) > 128:
                oldest = next(iter(self.entries))
                if oldest != key:
                    self.entries.pop(oldest)
        with entry['lock']:
            if ('value' not in entry or entry['version'] != version or
                    (ttl is not None and time.monotonic() >= entry['expires'])):
                value = build()  # failures never replace the last successful result
                entry.update(version=version, value=value, expires=time.monotonic() + (ttl or 0))
            return entry['value']
