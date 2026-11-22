"""Synchronize private compiler headers by exact bytes without rebuilding untouched files."""
import os
import shutil
import threading
import time
from pathlib import Path

# A header's bytes are read from disk once and reused while its size, inode, modification and change time are
# unchanged. The change time can't be set from a program, so restoring the modification time after a same-size edit
# still shows. A file touched in the last two seconds is never trusted (the clock's tick may hide a second edit).
_FRESH_NS = 2_000_000_000
_MAX_KEPT = 8192
_KEPT = {}  # path -> (stamp, bytes)
_KEPT_LOCK = threading.Lock()


def _stamp(stat):
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


def _header_bytes(path, entry, extension):
    stat = entry.stat()
    stamp = _stamp(stat)
    kept = _KEPT.get(path)
    if kept is not None and kept[0] == stamp:
        return kept[1]
    with open(path, 'rb') as handle:
        data = handle.read()
    if extension in _HEADERS:
        data = data.replace(b'\r\n', b'\n')
    if max(stat.st_mtime_ns, stat.st_ctime_ns) < time.time_ns() - _FRESH_NS:
        with _KEPT_LOCK:
            if len(_KEPT) >= _MAX_KEPT:
                _KEPT.clear()
            _KEPT[path] = (stamp, data)
    return data


_HEADERS = ('.h', '.hpp', '.hxx', '.inc', '.inl')


def read(roots):
    files = {}
    for directory in roots:
        root = Path(directory)
        if not root.exists():raise FileNotFoundError(root)
        if not root.is_dir():raise NotADirectoryError(root)
        pending = [(str(root), '')]
        while pending:
            folder, prefix = pending.pop()
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir():
                        if entry.is_symlink():return None
                        pending.append((entry.path, prefix + entry.name + os.sep))
                    else:
                        if not entry.is_file():raise shutil.SpecialFileError(entry.path)
                        extension = os.path.splitext(entry.name)[1].lower()
                        files[prefix + entry.name] = _header_bytes(entry.path, entry, extension)
    return files


def stage(inc,files,known=None):
    """Make `inc` hold exactly `files`. `known` (name -> (stamp, bytes)) remembers what this process last wrote
    there: a file whose size, modification, change time and inode still match and whose bytes are the ones wanted is not
    read back. Only this private copy is trusted that way; the project's own headers are checked by read()."""
    if inc.is_symlink() or inc.exists() and not inc.is_dir():inc.unlink()
    inc.mkdir(exist_ok=True)
    directories=set()
    for name in files:
        parent=os.path.dirname(name)
        while parent and parent not in directories:
            directories.add(parent);parent=os.path.dirname(parent)
    missing=dict(files)
    written={}
    pending=[(str(inc),'')]
    while pending:
        folder,prefix=pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                name=prefix+entry.name;path=entry.path
                if entry.is_symlink():
                    os.unlink(path)
                elif entry.is_dir(follow_symlinks=False):
                    if name in directories:pending.append((path,name+os.sep))
                    else:shutil.rmtree(path)
                elif name not in files or not entry.is_file(follow_symlinks=False):
                    os.unlink(path)
                else:
                    data=missing.pop(name)
                    stamp=_stamp(entry.stat(follow_symlinks=False))
                    kept=(known or {}).get(name)
                    if kept is not None and kept[0]==stamp and kept[1]==data:
                        written[name]=kept;continue
                    with open(path,'rb') as handle:current=handle.read()
                    if current!=data:
                        with open(path,'wb') as handle:handle.write(data)
                        stamp=_stamp(os.stat(path))
                    written[name]=(stamp,data)
    for name,data in missing.items():
        path=inc/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        written[name]=(_stamp(path.stat()),data)
    return written
