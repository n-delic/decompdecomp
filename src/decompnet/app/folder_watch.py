"""Whether a save folder changed since a moment, from the operating system's change notifications.

A forced progress check stats every saved file (Progress._signature): about 45 us a file, so 450 ms on a 9,900-file
save, twice per compile and under the project's lock. A watch tells instead whether anything in the folder changed
since a mark, and the check reuses the signature it acknowledged then.

A watch never says "unchanged" unless it is sure. What it saw in the kernel's queue is read before answering:
- Linux: inotify on the folder and its src/, best/ and include/ (the levels the signature reads). The kernel queues an
  event before the write returns, so reading the queue misses nothing. One inotify instance serves every watch
  (a user has 128 by default).
- macOS: FSEvents on the folder, with a fence. The FSEvents service reads the kernel's events a few milliseconds
  late, and FlushSync only delivers what it has read: an in-place edit just before a check was missed 498 times in
  500. So a check writes a fence file in a private folder on the same volume, watched by the same stream, and waits
  for its event: events arrive in order, so every earlier change has arrived too (0 missed in 300; about 12 ms a
  check). That is only worth it on a large save (WORTH_FILES); a small one is stat'ed as before.
- Windows and anything else, a failure, an overflowed queue: no watch, or "changed", and the check stats as before.

Any event counts, the app's own writes and `.tmp` files included: a spurious change only costs the full check it
would have made anyway. DECOMP_FOLDER_WATCH=0 turns watches off.
"""
from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import logging
import os
import struct
import sys
import threading
import uuid
from pathlib import Path

log = logging.getLogger('decompdecomp')
SUBFOLDERS = ('src', 'best', 'include')  # the folders Progress._signature lists; files deeper are not read
FENCE_SECONDS = 2  # a fence event that takes longer than this: the watch stops vouching (the full check runs)
WORTH_FILES = 1000  # macOS: a fence costs about what statting 300 files does; below this the full check is as quick


def enabled() -> bool:
    return os.environ.get('DECOMP_FOLDER_WATCH', '1') != '0'


class FolderWatch:
    """Counts changes in one folder. mark() before reading the folder; unchanged_since(mark) afterwards."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.changes = 0
        self.broken = False
        self.lock = threading.Lock()

    def changed(self):
        with self.lock:
            self.changes += 1

    def mark(self) -> int | None:
        """The moment a reading of the folder starts; None when this watch can't vouch for anything. Changes from
        before it are taken in first (the app's own writes just made), so they don't count as later ones."""
        if self.broken:
            return None
        self._settle()
        return None if self.broken else self.changes

    def worth(self, files: int) -> bool:
        """Whether asking this watch is quicker than statting `files` files."""
        return True

    def unchanged_since(self, mark: int | None) -> bool:
        if mark is None or self.broken:
            return False
        self._settle()
        return not self.broken and self.changes == mark

    def _settle(self):
        """Take in every change that happened before this call."""

    def close(self):
        self.broken = True


# ---------------------------------------------------------------- Linux: inotify

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_Q_OVERFLOW, IN_IGNORED = 0x400, 0x800, 0x4000, 0x8000
IN_ONLYDIR, IN_DONT_FOLLOW = 0x01000000, 0x02000000
IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
WATCHED = (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
           | IN_DELETE_SELF | IN_MOVE_SELF | IN_ONLYDIR | IN_DONT_FOLLOW)
_EVENT = struct.Struct('iIII')


class _Inotify:
    """The process's one inotify instance: watch descriptors -> the folder watches that asked for them."""

    def __init__(self):
        self.libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
        self.libc.inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
        self.fd = self.libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1 failed')
        self.owners: dict[int, set] = {}  # wd -> watches
        self.lock = threading.Lock()

    def add(self, watch, path: Path) -> int | None:
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), WATCHED)
        if wd < 0:
            return None  # (missing, not a folder, a symlink: the signature doesn't read inside it either)
        with self.lock:
            self.owners.setdefault(wd, set()).add(watch)
        return wd

    def remove(self, watch, wds):
        with self.lock:
            for wd in wds:
                owners = self.owners.get(wd)
                if owners is not None:
                    owners.discard(watch)
                    if not owners:
                        del self.owners[wd]
                        self.libc.inotify_rm_watch(self.fd, wd)

    def drain(self):
        """Read every queued event: the kernel queued them before the writes that caused them returned."""
        with self.lock:
            while True:
                try:
                    data = os.read(self.fd, 65536)
                except BlockingIOError:
                    return
                offset = 0
                while offset + _EVENT.size <= len(data):
                    wd, mask, _, length = _EVENT.unpack_from(data, offset)
                    offset += _EVENT.size + length
                    if mask & IN_Q_OVERFLOW:
                        for owners in self.owners.values():
                            for watch in owners:
                                watch.changes += 1  # (we hold no watch's lock here: counts only grow)
                        continue
                    for watch in self.owners.get(wd, ()):
                        watch.changes += 1
                        if mask & IN_IGNORED:
                            watch.lost(wd)
                    if mask & IN_IGNORED:
                        self.owners.pop(wd, None)


_inotify = None
_inotify_lock = threading.Lock()


def _shared_inotify():
    global _inotify
    with _inotify_lock:
        if _inotify is None:
            _inotify = _Inotify()
        return _inotify


class InotifyWatch(FolderWatch):
    def __init__(self, folder: Path):
        super().__init__(folder)
        self.inotify = _shared_inotify()
        self.wds: dict[Path, int] = {}
        self._ensure()

    def _ensure(self):
        """Watch the folder and each subfolder the signature lists, again when one was replaced or created."""
        for path in (self.folder, *(self.folder / name for name in SUBFOLDERS)):
            wd = self.inotify.add(self, path)
            if wd is None:
                if path == self.folder:
                    self.broken = True
                self.wds.pop(path, None)
            else:
                self.wds[path] = wd

    def lost(self, wd):
        for path, known in list(self.wds.items()):
            if known == wd:
                del self.wds[path]

    def mark(self):
        self._ensure()  # (a subfolder created since has its own watch from here on; its creation was a change)
        return super().mark()

    def _settle(self):
        self.inotify.drain()

    def close(self):
        super().close()
        self.inotify.remove(self, self.wds.values())
        self.wds.clear()


# ---------------------------------------------------------------- macOS: FSEvents

_FILE_EVENTS, _NO_DEFER, _WATCH_ROOT = 0x10, 0x02, 0x04
_HISTORY_DONE = 0x10  # (an event flag: the replay of what happened before the stream started is over)
_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))
_DISPATCH_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_mac = None
_mac_lock = threading.Lock()


class _MacLibraries:
    def __init__(self):
        cs = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        system = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32)
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p)
        cf.CFRelease.argtypes = (ctypes.c_void_p,)
        cs.FSEventStreamCreate.restype = ctypes.c_void_p
        cs.FSEventStreamCreate.argtypes = (ctypes.c_void_p, _CALLBACK, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32)
        cs.FSEventsGetCurrentEventId.restype = ctypes.c_uint64
        for name in ('FSEventStreamStart',):
            getattr(cs, name).restype = ctypes.c_bool
        for name in ('FSEventStreamStart', 'FSEventStreamFlushSync', 'FSEventStreamStop', 'FSEventStreamInvalidate',
                     'FSEventStreamRelease'):
            getattr(cs, name).argtypes = (ctypes.c_void_p,)
        cs.FSEventStreamSetDispatchQueue.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        system.dispatch_queue_create.restype = ctypes.c_void_p
        system.dispatch_queue_create.argtypes = (ctypes.c_char_p, ctypes.c_void_p)
        system.dispatch_sync_f.restype = None
        system.dispatch_sync_f.argtypes = (ctypes.c_void_p, ctypes.c_void_p, _DISPATCH_CALLBACK)
        self.cs, self.cf = cs, cf
        self.dispatch_sync = system.dispatch_sync_f
        self.queue_marker = _DISPATCH_CALLBACK(lambda _: None)
        self.array_callbacks = ctypes.addressof(ctypes.c_void_p.in_dll(cf, 'kCFTypeArrayCallBacks'))
        self.queue = system.dispatch_queue_create(b'decompdecomp.folder-watch', None)
        self.streams = set()
        atexit.register(self.stop_all)  # no callback may reach a finalizing interpreter

    def release_stream(self, stream, *, started):
        if started:
            self.cs.FSEventStreamStop(stream)
        self.cs.FSEventStreamInvalidate(stream)
        # Invalidation unschedules the stream. Wait for work already submitted
        # to the serial queue before releasing the native stream and its Python callback.
        self.dispatch_sync(self.queue, None, self.queue_marker)
        self.cs.FSEventStreamRelease(stream)

    def stop_all(self):
        for watch in list(self.streams):
            watch.close()


def _mac_libraries():
    global _mac
    with _mac_lock:
        if _mac is None:
            _mac = _MacLibraries()
        return _mac


class FSEventsWatch(FolderWatch):
    def __init__(self, folder: Path, fence_folder: Path):
        super().__init__(folder)
        lib = self.lib = _mac_libraries()
        fence_folder.mkdir(parents=True, exist_ok=True)
        self.fence = Path(os.path.realpath(fence_folder)) / f'{os.getpid()}-{uuid.uuid4().hex}'
        self.fence_folder = os.fsencode(self.fence.parent) + b'/'  # (other watches' fences are there too)
        self.fence_number = 0
        self.pending_fence = None
        self.fence_arrived = False
        self.settle_lock = threading.Lock()
        # Writes from just before the stream started arrive twice, replayed and live, and may follow the first fence:
        # until the replay is over the watch vouches for nothing.
        self.history_done = False
        self.arrived = threading.Condition(self.lock)
        self.callback = _CALLBACK(self._events)  # (kept: the stream calls it for as long as it runs)
        names = [lib.cf.CFStringCreateWithCString(None, os.fsencode(os.path.realpath(p)), 0x08000100)
                 for p in (folder, fence_folder)]
        paths = lib.cf.CFArrayCreate(None, (ctypes.c_void_p * 2)(*names), 2, lib.array_callbacks)
        try:
            since = lib.cs.FSEventsGetCurrentEventId()
            self.stream = lib.cs.FSEventStreamCreate(None, self.callback, None, paths, since, 0.0,
                                                     _FILE_EVENTS | _NO_DEFER | _WATCH_ROOT)
        finally:
            lib.cf.CFRelease(paths)
            for name in names:
                lib.cf.CFRelease(name)
        if not self.stream:
            raise OSError('FSEventStreamCreate failed')
        lib.cs.FSEventStreamSetDispatchQueue(self.stream, lib.queue)
        if not lib.cs.FSEventStreamStart(self.stream):
            lib.release_stream(self.stream, started=False)
            self.stream = None
            raise OSError('FSEventStreamStart failed')
        lib.streams.add(self)

    def _events(self, stream, info, count, paths, flags, ids):
        names = ctypes.cast(paths, ctypes.POINTER(ctypes.c_char_p))
        with self.lock:
            for index in range(count):
                if flags[index] & _HISTORY_DONE and not flags[index] & ~_HISTORY_DONE:
                    self.history_done = True
                    continue
                name = names[index] or b''
                if name == self.pending_fence:
                    self.fence_arrived = True
                elif not name.startswith(self.fence_folder):
                    self.changes += 1
            self.arrived.notify_all()

    def worth(self, files: int) -> bool:
        return files >= WORTH_FILES

    def _settle(self):
        """Wait for this check's unique fence; a delayed earlier event cannot vouch for it.

        Rewriting one filename can be coalesced without another native event. Each
        check creates a fresh filename, and acknowledges only that exact token.
        """
        with self.settle_lock:
            if self.broken:
                return
            with self.lock:
                self.fence_number += 1
                fence = self.fence.with_name(f'{self.fence.name}-{self.fence_number}')
                self.pending_fence = os.fsencode(fence)
                self.fence_arrived = False
            try:
                try:
                    fence.write_bytes(b'')
                except OSError:
                    self.broken = True
                    return
                with self.lock:
                    if not self.arrived.wait_for(lambda: (self.fence_arrived and self.history_done) or self.broken,
                                                 timeout=FENCE_SECONDS):
                        self.broken = True
                        log.info('Save folder watch stopped: the fence took over %ss; changes are checked file by file',
                                 FENCE_SECONDS)
            finally:
                with self.lock:
                    self.pending_fence = None
                try:
                    fence.unlink(missing_ok=True)
                except OSError:
                    pass

    def close(self):
        super().close()
        with _mac_lock:
            stream, self.stream = getattr(self, 'stream', None), None
            if stream:
                self.lib.release_stream(stream, started=True)
        self.lib.streams.discard(self)
        with self.lock:
            self.arrived.notify_all()



def worth(files: int) -> bool:
    """Whether a watch is quicker here than statting `files` files: always with inotify, on a large save with a fence."""
    if not enabled():
        return False
    if sys.platform.startswith('linux'):
        return True
    return sys.platform == 'darwin' and files >= WORTH_FILES


def _same_volume(folder: Path, candidates) -> Path | None:
    try:
        device = os.stat(folder).st_dev
    except OSError:
        return None
    for candidate in candidates:
        probe = candidate
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent  # (the fence folder itself is made when used)
        try:
            if os.stat(probe).st_dev == device:
                return candidate
        except OSError:
            continue
    return None


def open_watch(folder: Path, fence_folders=()) -> FolderWatch | None:
    """A watch of `folder`, or None where none can vouch for it (Windows for now: ROADMAP.md). macOS needs a fence
    folder of the app's own on the same volume, never inside the save (`fence_folders`, the first that fits)."""
    if not enabled():
        return None
    try:
        if sys.platform.startswith('linux'):
            watch = InotifyWatch(folder)
        elif sys.platform == 'darwin':
            fence = _same_volume(folder, [Path(f) for f in fence_folders])
            if fence is None:
                return None
            watch = FSEventsWatch(folder, fence)
        else:
            return None
    except (OSError, AttributeError, ValueError) as error:  # (AttributeError: a libc without inotify)
        log.info('Save folder changes are checked file by file: %s', error)
        return None
    if watch.broken:
        watch.close()
        return None
    return watch
