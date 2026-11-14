"""Bounded filesystem facts for SQLite storage failures, without SQL, paths or payloads."""
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

log = logging.getLogger("decompdecomp")
_lock = threading.Lock()
_last = {}
SECONDS = 60
_CODES = {sqlite3.SQLITE_CANTOPEN: 'CANTOPEN', sqlite3.SQLITE_IOERR: 'IOERR', sqlite3.SQLITE_FULL: 'FULL'}
_OPERATIONS = {'connect','execute','executemany','executescript','fetchone','fetchmany','fetchall','__next__',
               'commit','rollback','__enter__','__exit__','close','cursor'}


def _path(database):
    value = os.fsdecode(database)
    if value.startswith('file:'):
        parsed = urlsplit(value)
        if 'mode=memory' in parsed.query:return None
        value = unquote(parsed.path)
    return None if value in ('', ':memory:') else Path(value)


def _scope(path):
    if path is None:return 'memory'
    if path.name == 'match-library.sqlite3':return 'match-library'
    if path.name.endswith('.transcripts.sqlite3'):return 'transcripts'
    if path.name == 'decomp.db':return 'root'
    return 'project' if path.parent.name == 'project-databases' else 'other'


def _storage(path):
    if path is None:return (-1, -1, -1, -1)
    try:
        return (int(path.exists()), int(path.parent.is_dir()), int(os.access(path.parent, os.W_OK)),
                shutil.disk_usage(path.parent).free // (1024*1024))
    except OSError:
        return (int(path.exists()), int(path.parent.is_dir()), -1, -1)


def _free(path):
    try:return shutil.disk_usage(path).free // (1024*1024)
    except OSError:return -1


def _descriptors():
    try:
        with os.scandir('/dev/fd') as entries:
            count = 0
            for _ in entries:
                count += 1
                if count >= 8192:break
        return count
    except OSError:return -1


def report(error, database, operation):
    """Observe only storage failures; callers must re-raise the original SQLite exception.

    SQLite's configured temp directory can differ from TMPDIR; these are environmental
    facts at failure time, not a diagnosis or permission to retry a write.
    """
    code = getattr(error, 'sqlite_errorcode', None)
    if not isinstance(code, int) or code & 255 not in _CODES:return
    try:
        path = _path(database)
        scope = _scope(path)
        operation = operation if operation in _OPERATIONS else 'other'
        key = (scope, operation, code & 255)
        now = time.monotonic()
        with _lock:
            if now - _last.get(key, float('-inf')) < SECONDS:return
            # All keys are drawn from fixed, small sets, never database paths or errors.
            _last[key] = now
        exists, parent, writable, free = _storage(path)
        configured = os.environ.get('SQLITE_TMPDIR') or os.environ.get('TMPDIR')
        temp = Path(configured) if configured else None
        temp_exists = int(temp.is_dir()) if temp is not None else -1
        temp_free = _free(temp) if temp_exists == 1 else -1
        try:
            import resource
            limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        except (ImportError, ValueError, OSError):limit = -1
        log.warning("SQLite storage failure code=%d category=%s scope=%s operation=%s "
                    "database_exists=%d parent_exists=%d parent_writable=%d free_mib=%d "
                    "configured_temp_exists=%d configured_temp_free_mib=%d open_fds=%d fd_limit=%d fd_sample_cap=8192",
                    code, _CODES[code & 255], scope, operation, exists, parent, writable, free,
                    temp_exists, temp_free, _descriptors(), limit)
    except (OSError, ValueError, TypeError, RuntimeError):
        # Optional metadata collection must never replace the original database failure.
        return
