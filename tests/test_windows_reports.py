"""Problems a Windows user hit: a stale instance lock, a slow first copy into WSL, a blocked portable ZIP."""
import os
import subprocess
import sys

import pytest

from decompnet import wsl
from decompnet.app import instance_lock, window


def test_windows_never_probes_a_pid_with_os_kill(monkeypatch):
    monkeypatch.setattr(instance_lock, "_windows_alive", lambda pid: False)
    monkeypatch.setattr(instance_lock.os, "kill", lambda *_: pytest.fail("os.kill(pid, 0) is Ctrl+C on Windows"))
    monkeypatch.setattr(instance_lock.os, "name", "nt")
    try:
        assert instance_lock.alive(12345) is False
    finally:
        monkeypatch.undo()


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows process API")
def test_a_stale_owner_is_seen_as_ended():
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    assert instance_lock.alive(os.getpid())
    assert not instance_lock.alive(ended.pid)


def test_a_copy_into_wsl_that_times_out_says_what_to_do(monkeypatch, tmp_path):
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / ".decompnet-toolchain.json").write_text("{}")
    monkeypatch.setattr(wsl, "_cache_directory", lambda: "/home/u/cache")
    monkeypatch.setattr(wsl, "path", str)

    def run(argv, cwd, timeout, **_):
        assert timeout >= 600  # a whole toolchain read from a Windows drive
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(wsl, "run", run)
    with pytest.raises(OSError, match="wsl --shutdown"):
        wsl.prepare_tools((tool,))


def test_unblock_removes_the_download_mark_from_every_library(monkeypatch, tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.dll").write_bytes(b"")
    (tmp_path / "a" / "y.txt").write_bytes(b"")
    removed = []

    def remove(path):
        removed.append(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(window.os, "remove", remove)
    window._unblock(tmp_path)
    assert removed == [f"{tmp_path / 'a' / 'b' / 'x.dll'}:Zone.Identifier"]
