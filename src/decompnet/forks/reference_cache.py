"""Bounded content-keyed caches for parsed imported reference objects."""
from collections import OrderedDict
from pathlib import Path
from threading import RLock


class ChangedDuringRead(Exception):
    """A reference was replaced or modified while it was being read."""


def _stamp(info) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def read(path: Path) -> bytes:
    """Read an object only when its file identity stays stable across the read."""
    import os

    with path.open('rb') as source:
        before = _stamp(os.fstat(source.fileno()))
        raw = source.read()
        after = _stamp(os.fstat(source.fileno()))
    if before != after or after != _stamp(path.stat()):
        raise ChangedDuringRead
    return raw


class Cache:
    def __init__(self, limit: int = 2048):
        self.limit = limit
        self.lock = RLock()
        self.values = OrderedDict()

    def get(self, key, make):
        with self.lock:
            if key in self.values:
                self.values.move_to_end(key)
                return self.values[key]
        value = make()
        with self.lock:
            self.values[key] = value
            self.values.move_to_end(key)
            while len(self.values) > self.limit:
                self.values.popitem(last=False)
        return value
