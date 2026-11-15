"""A fence acknowledges exactly its own write, even with delayed/coalesced native events."""
import ctypes
import os
import threading

from decompnet.app import folder_watch as fw


def watch_without_native_stream(tmp_path):
    watch = object.__new__(fw.FSEventsWatch)
    fw.FolderWatch.__init__(watch, tmp_path / 'save')
    watch.fence = tmp_path / 'fence-token'
    watch.fence_prefix = os.fsencode(watch.fence)
    watch.fence_folder = os.fsencode(tmp_path) + b'/'
    watch.fences = 0  # baseline implementation's event counter
    watch.fence_number = 0
    watch.pending_fence = None
    watch.fence_arrived = False
    watch.settle_lock = threading.Lock()
    watch.history_done = True
    watch.arrived = threading.Condition(watch.lock)
    return watch


def deliver(watch, path):
    names = (ctypes.c_char_p * 1)(os.fsencode(path))
    flags = (ctypes.c_uint32 * 1)(0x1000)
    ids = (ctypes.c_uint64 * 1)(1)
    watch._events(None, None, 1, names, flags, ids)


def test_each_check_writes_a_distinct_fence_and_cleans_it_up(tmp_path, monkeypatch):
    watch = watch_without_native_stream(tmp_path)
    paths = []
    write = type(watch.fence).write_bytes

    def acknowledge(path, data):
        paths.append(path)
        result = write(path, data)
        deliver(watch, path)
        return result

    monkeypatch.setattr(type(watch.fence), 'write_bytes', acknowledge)
    marks = [watch.mark() for _ in range(4)]
    assert marks == [0] * 4
    assert len(set(paths)) == 4  # modifying one filename can be coalesced by FSEvents
    assert not any(path.exists() for path in paths)


def test_old_or_prefix_neighbor_event_cannot_acknowledge_current_fence(tmp_path, monkeypatch):
    watch = watch_without_native_stream(tmp_path)
    written = threading.Event()
    done = threading.Event()
    paths = []
    write = type(watch.fence).write_bytes

    def observe(path, data):
        result = write(path, data)
        paths.append(path)
        written.set()
        return result

    monkeypatch.setattr(type(watch.fence), 'write_bytes', observe)
    monkeypatch.setattr(fw, 'FENCE_SECONDS', 1)

    def settle():
        try:watch.mark()
        finally:done.set()

    thread = threading.Thread(target=settle)
    thread.start()
    try:
        assert written.wait(1)
        deliver(watch, watch.fence.with_name(watch.fence.name + '-old'))
        assert not done.wait(.03)
        deliver(watch, paths[0])
        assert done.wait(1)
        assert not watch.broken
    finally:
        if paths:deliver(watch, paths[0])
        thread.join(2)
    assert not thread.is_alive()


def test_missing_fence_still_breaks_watch_and_cleans_private_file(tmp_path, monkeypatch):
    watch = watch_without_native_stream(tmp_path)
    monkeypatch.setattr(fw, 'FENCE_SECONDS', .01)
    assert watch.mark() is None
    assert watch.broken
    assert list(tmp_path.iterdir()) == []


def test_fence_write_error_keeps_full_check_fallback(tmp_path, monkeypatch):
    watch = watch_without_native_stream(tmp_path)

    def fail(path, data):
        raise OSError('synthetic fence write failure')

    monkeypatch.setattr(type(watch.fence), 'write_bytes', fail)
    assert watch.mark() is None
    assert watch.broken
    assert watch.pending_fence is None
    assert list(tmp_path.iterdir()) == []


def test_close_wakes_fence_waiter_and_cleans_its_file(tmp_path, monkeypatch):
    from types import SimpleNamespace

    watch = watch_without_native_stream(tmp_path)
    watch.lib = SimpleNamespace(streams={watch})
    watch.stream = None
    written = threading.Event()
    results = []
    write = type(watch.fence).write_bytes

    def observe(path, data):
        result = write(path, data)
        written.set()
        return result

    monkeypatch.setattr(type(watch.fence), 'write_bytes', observe)
    thread = threading.Thread(target=lambda: results.append(watch.mark()))
    thread.start()
    try:
        assert written.wait(1)
        watch.close()
    finally:
        watch.close()
        thread.join(2)
    assert not thread.is_alive()
    assert results == [None]
    assert watch.pending_fence is None
    assert list(tmp_path.iterdir()) == []
