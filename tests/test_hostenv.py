"""Paths the user types: on Windows decompdecomp runs in WSL, where a path copied from Explorer is under /mnt."""

from pathlib import Path

from decompnet import hostenv


def _kernel(monkeypatch, tmp_path, release: str) -> None:
    f = tmp_path / "osrelease"
    f.write_text(release, encoding="utf-8")
    monkeypatch.setattr(hostenv, "KERNEL_RELEASE", f)


def test_a_windows_path_typed_in_wsl_is_the_same_folder_under_mnt(monkeypatch, tmp_path):
    _kernel(monkeypatch, tmp_path, "6.6.87.2-microsoft-standard-WSL2")
    assert hostenv.local_path(r"C:\Users\sam\Games\demo") == Path("/mnt/c/Users/sam/Games/demo")
    assert hostenv.local_path('"D:/Program Files/Games"') == Path("/mnt/d/Program Files/Games")
    assert hostenv.local_path("E:") == Path("/mnt/e")
    assert hostenv.local_path("/home/sam/games") == Path("/home/sam/games")  # a Linux path stays as it is


def test_elsewhere_a_path_is_taken_as_written(monkeypatch, tmp_path):
    _kernel(monkeypatch, tmp_path, "6.8.0-45-generic")
    monkeypatch.setattr(hostenv.sys, "platform", "linux")  # the tests run on Windows too
    assert hostenv.local_path(" '/opt/games' ") == Path("/opt/games")
    assert hostenv.local_path(r"C:\Games") == Path(r"C:\Games")
    assert hostenv.local_path("/mnt/c/Games") == Path("/mnt/c/Games")  # on Linux, a Linux path


def test_the_native_windows_version_reads_wsl_and_git_bash_paths_as_windows_ones(monkeypatch):
    assert hostenv.windows_path("/mnt/c/Users/sam/Games/demo") == r"C:\Users\sam\Games\demo"
    assert hostenv.windows_path("/d/Program Files/Games/") == r"D:\Program Files\Games"
    assert hostenv.windows_path("/c") == "C:\\"
    assert hostenv.windows_path("/home/sam/games") is None and hostenv.windows_path(r"C:\Games") is None
    monkeypatch.setattr(hostenv.sys, "platform", "win32")
    assert str(hostenv.local_path('"/mnt/e/Games"')) == r"E:\Games"
