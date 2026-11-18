"""Answers that only change when a file or the environment does, kept until it does.

A deep search compiles a hundred candidates a second from one interpreter, and each compile re-read the
toolchain manifests and re-sorted every environment variable to prove nothing had changed. These keep the
proof (a file's size, modification and change time and inode: a write moves its change time even when its modification time is put back; a dict comparison of the environment) and drop the work.
"""
import os
import threading
from collections import OrderedDict

_ENVIRONMENT = ({}, ())
_FILES = OrderedDict()
_LOCK = threading.Lock()


def environment() -> tuple:
    """sorted(os.environ.items()) as a tuple, re-sorted only when os.environ changes."""
    global _ENVIRONMENT
    seen, ordered = _ENVIRONMENT
    current = os.environ._data
    if current != seen:
        ordered = tuple(sorted(os.environ.items()))
        _ENVIRONMENT = (dict(current), ordered)
    return ordered


def read_bytes(path) -> bytes:
    """path's bytes, read again only when its size, modification or change time or inode changes. Raises OSError as
    Path.read_bytes does. For small files (manifests): at most 256 are kept."""
    stat = os.stat(path)
    key = os.fspath(path)
    stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    with _LOCK:
        kept = _FILES.get(key)
        if kept is not None and kept[0] == stamp:
            _FILES.move_to_end(key)
            return kept[1]
    with open(path, 'rb') as file:
        data = file.read()
    with _LOCK:
        _FILES[key] = (stamp, data)
        while len(_FILES) > 256:
            _FILES.popitem(last=False)
    return data
