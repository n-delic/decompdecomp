"""Windows' native window and desktop shortcut integration, exercised without WebView2 or a desktop."""

import base64
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet.app import edition, launcher


def test_windows_launcher_creates_a_desktop_shortcut(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("DECOMP_PACKAGE", raising=False)
    monkeypatch.setattr(launcher, "_windows_folder", lambda folder: {
        0x10: desktop, 0x02: tmp_path / "Programs", 0x1c: tmp_path / "Local"}[folder])
    monkeypatch.setattr(launcher, "_decomp_exe", lambda: r"C:\Apps\decomp.exe")
    monkeypatch.setattr(launcher, "_shortcut_app_id", lambda _: None)
    ran = []

    def run(args, **kwargs):
        ran.append((args, kwargs))
        (desktop / "decompdecomp.lnk").write_bytes(b"shortcut")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(edition, "DEV", False)
    assert "desktop" in launcher.install_launcher()
    assert (desktop / "decompdecomp.lnk").exists()
    assert (tmp_path / "Local" / "decompnet" / "decompdecomp.ico").read_bytes()[:4] == b"\0\0\1\0"
    assert not (tmp_path / "Local" / "decompdecomp" / "bin" / "decomp.cmd").exists()  # a fresh install has no PATH
    assert ran and ran[0][0][0] == "powershell.exe"
    script = base64.b64decode(ran[0][0][-1]).decode("utf-16le")
    assert "pythonw.exe" in script and "-m decompnet.app.cli" in script and "Programs" in script


def test_windows_msi_launcher_starts_the_bundled_runtime(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(launcher.sys, "executable", r"C:\Apps\python\python.exe")
    monkeypatch.setattr(launcher, "_windows_folder", lambda folder: {
        0x10: desktop, 0x02: tmp_path / "Programs", 0x1c: tmp_path / "Local"}[folder])
    monkeypatch.setattr(launcher, "_shortcut_app_id", lambda _: None)
    monkeypatch.setattr(edition, "DEV", False)
    monkeypatch.setenv("DECOMP_PACKAGE", "windows")
    ran = []

    def run(args, **kwargs):
        ran.append(args)
        (desktop / "decompdecomp.lnk").write_bytes(b"shortcut")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.install_launcher()
    script = base64.b64decode(ran[0][-1]).decode("utf-16le")
    assert r"C:\Apps\python\pythonw.exe" in script
    assert r"C:\Apps\decomp-launch.pyw" in script
    assert "-m decompnet.app.cli" not in script


def test_windows_development_copy_never_installs_shortcuts(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(edition, "DEV", True)
    monkeypatch.setattr(launcher, "_windows_folder", lambda *_: pytest.fail("touched Windows shell folders"))
    assert "no launcher" in launcher.install_launcher()


def _windows_command_test(monkeypatch, tmp_path, executable, bundled):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher.sys, "executable", executable)
    monkeypatch.setattr(edition, "DEV", False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    if bundled:
        monkeypatch.setenv("DECOMP_PACKAGE", "windows")
    else:
        monkeypatch.delenv("DECOMP_PACKAGE", raising=False)
    return tmp_path / "Local" / "decompdecomp" / "bin" / "decomp.cmd"


def test_the_windows_command_wrapper_keeps_every_argument_whole(monkeypatch, tmp_path):
    """PowerShell hands a .cmd file one command line; `%*` keeps each argument as typed (spaces, slashes, a trailing
    backslash), where `%1 %2` would split a path. The wrapper's own paths are quoted, so an install folder with a
    space works, and it isn't `start`ed (which would take the first quoted argument for a window title)."""
    path = _windows_command_test(monkeypatch, tmp_path,
                                 r"C:\Program Files\decompdecomp\python\pythonw.exe", bundled=True)
    assert launcher.write_windows_command(path) == path
    text = path.read_text(encoding="utf-8")
    assert "%*" in text and "%1 " not in text
    assert not any(line.strip().lower().startswith("start ") for line in text.splitlines())
    assert '"C:\\Program Files\\decompdecomp\\python\\python.exe"' in text
    assert '"C:\\Program Files\\decompdecomp\\decomp-launch.pyw"' in text
    assert "exit /b %ERRORLEVEL%" in text
    assert b"\r\n" in path.read_bytes()


def test_the_old_wsl_command_is_replaced_and_a_users_own_is_left_alone(monkeypatch, tmp_path):
    """1.0.2's command stays in %LOCALAPPDATA%\\decompdecomp\\bin, in front of this app on PATH. It ran the old WSL
    distribution, so an upgrade still got its commands (`decomp host`) instead of this app's. Only a command this
    project wrote is replaced; one the user wrote is theirs."""
    path = _windows_command_test(monkeypatch, tmp_path,
                                 r"C:\Apps\decompdecomp\python\pythonw.exe", bundled=True)
    path.parent.mkdir(parents=True)
    path.write_text('@echo off\n"%SystemRoot%\\System32\\wsl.exe" -d decomp-wtf --cd ~ '
                    '--exec /usr/local/bin/decomp %*\n')
    assert launcher.update_windows_command() == path
    text = path.read_text(encoding="utf-8")
    assert "wsl.exe" not in text and "/usr/local/bin/decomp" not in text
    assert "decomp-launch.pyw" in text and "%*" in text
    mine = "@echo off\necho my own decomp\n"
    path.write_text(mine)
    assert launcher.update_windows_command() is None
    assert path.read_text(encoding="utf-8") == mine
    path.unlink()
    assert launcher.update_windows_command() is None  # and an app start doesn't put one where there was none


def test_the_windows_command_of_a_source_install_runs_the_module(monkeypatch, tmp_path):
    path = _windows_command_test(monkeypatch, tmp_path, r"C:\Tools\decomp\Scripts\python.exe", bundled=False)
    assert launcher.write_windows_command(path) == path
    text = path.read_text(encoding="utf-8")
    assert "-m" in text and "decompnet.app.cli" in text and "decomp-launch.pyw" not in text


@pytest.mark.parametrize("path", [r"C:\Users\Some User\Project", "C:/Users/Some User/Project",
                                  "C:\\Users\\Some User\\Project\\"])
def test_the_cli_reads_a_windows_path_as_one_argument(monkeypatch, path):
    from decompnet.app import cli

    seen = []
    monkeypatch.setattr(cli, "install_info", lambda args: seen.append(args.repo) or 0)
    assert cli.main(["install-info", "--repo", path]) == 0
    assert seen == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows shortcut property stores")
def test_real_shortcuts_launch_without_a_console_and_group_with_the_window(monkeypatch, tmp_path):
    from win32com.client import Dispatch
    from win32com.propsys import propsys

    folders = {0x10: tmp_path / "Redirected Desktop", 0x02: tmp_path / "Start menu", 0x1c: tmp_path / "Local"}
    monkeypatch.setattr(launcher, "_windows_folder", folders.__getitem__)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(edition, "DEV", False)
    assert "Start menu" in launcher.install_launcher()
    for folder in (folders[0x10], folders[0x02]):
        path = folder / "decompdecomp.lnk"
        shortcut = Dispatch("WScript.Shell").CreateShortcut(str(path))
        assert Path(shortcut.TargetPath).name == "pythonw.exe"
        assert shortcut.Arguments == "-m decompnet.app.cli"
        store = propsys.SHGetPropertyStoreFromParsingName(str(path))
        assert store.GetValue(propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")).GetValue() == "decompdecomp"
