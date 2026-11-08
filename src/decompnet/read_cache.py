"""Bounded, single-flight caches for derived read-only results; callers supply input revisions."""
import threading
import time
from collections import OrderedDict

READ_WAIT_SECONDS = 10


class ReadPending(TimeoutError):
    """Another reader owns this metadata build; retry without treating it as a broken tool."""


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
            if not entry['lock'].acquire(timeout=READ_WAIT_SECONDS):
                raise ReadPending('Shared metadata is still being prepared. Try again shortly.')
            try:
                if entry.get('builder') == threading.get_ident():
                    raise RuntimeError('A read cache cannot recursively build its own entry.')
                if ('value' not in entry or entry['version'] != version or
                        (ttl is not None and time.monotonic() >= entry['expires'])):
                    entry['builder'] = threading.get_ident()
                    try:
                        value = build()  # failures never replace the last successful result
                        entry.update(version=version, value=value, expires=time.monotonic() + (ttl or 0))
                    finally:
                        entry.pop('builder', None)
                return entry['value']
            finally:
                entry['lock'].release()
        finally:
            with self.lock:
                entry['users'] -= 1
                self._trim()
