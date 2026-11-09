"""Exercise Toolhelp traversal without requiring Windows or ending real processes."""
from types import SimpleNamespace

import pytest

from decompnet import winprocess


class Call:
    """ctypes functions have configurable argtypes/restype as well as being callable."""
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.mark.parametrize('entries,expected', [
    ([(100, 10), (200, 100), (300, 200)], [300, 200, 100]),
    ([(100, 100), (200, 100)], [200, 100]),
    ([(100, 200), (200, 100)], [200, 100]),
    ([(200, 100), (200, 100), (300, 200)], [300, 200, 100]),
    ([], [100]),
])
def test_terminate_tree_handles_reused_ids_and_stops_children_first(monkeypatch, entries, expected):
    remaining = iter(entries)
    opened, stopped, closed = [], [], []
    def next_process(snapshot, pointer):
        entry = next(remaining, None)
        if entry is None:
            return False
        pointer._obj.th32ProcessID, pointer._obj.th32ParentProcessID = entry
        return True
    def open_process(access, inherit, pid):
        assert access == winprocess.PROCESS_TERMINATE and not inherit
        opened.append(pid)
        return pid + 1000
    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Call(lambda flags, pid: 999),
        Process32FirstW=Call(next_process), Process32NextW=Call(next_process),
        OpenProcess=Call(open_process),
        TerminateProcess=Call(lambda handle, code: stopped.append(handle - 1000)),
        CloseHandle=Call(lambda handle: closed.append(handle)),
    )
    monkeypatch.setattr(winprocess.ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
    winprocess.terminate_tree(100)
    assert opened == stopped == expected
    assert closed == [999, *(pid + 1000 for pid in expected)]
