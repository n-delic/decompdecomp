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
    assert ran and ran[0][0][0] == "powershell.exe"
    script = base64.b64decode(ran[0][0][-1]).decode("utf-16le")
    assert "pythonw.exe" in script and "-m decompnet.app.cli" in script and "Programs" in script


def test_windows_msi_launcher_starts_the_bundled_runtime(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
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


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows shortcut property stores")
def test_real_shortcuts_launch_without_a_console_and_group_with_the_window(monkeypatch, tmp_path):
    from win32com.client import Dispatch
    from win32com.propsys import propsys

    folders = {0x10: tmp_path / "Redirected Desktop", 0x02: tmp_path / "Start menu", 0x1c: tmp_path / "Local"}
    monkeypatch.setattr(launcher, "_windows_folder", folders.__getitem__)
    monkeypatch.setattr(edition, "DEV", False)
    assert "Start menu" in launcher.install_launcher()
    for folder in (folders[0x10], folders[0x02]):
        path = folder / "decompdecomp.lnk"
        shortcut = Dispatch("WScript.Shell").CreateShortcut(str(path))
        assert Path(shortcut.TargetPath).name == "pythonw.exe"
        assert shortcut.Arguments == "-m decompnet.app.cli"
        store = propsys.SHGetPropertyStoreFromParsingName(str(path))
        assert store.GetValue(propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")).GetValue() == "decompdecomp"
