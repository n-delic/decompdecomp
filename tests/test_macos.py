"""Mac integration points, with isolated launchers and no dialogs or real agents in the regular test run."""

import functools
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import hostenv, toolchains
from decompnet.app import agents as app_agents
from decompnet.app import launcher, macwindow, picker, window


@pytest.mark.parametrize("url,kind", [
    ("http://127.0.0.1:6474/api/export?file=1", "internal"),
    ("http://127.0.0.1:64740/", "external"),
    ("http://127.0.0.1:6474.evil.example/", "external"),
    ("https://example.com/", "external"), ("https://127.0.0.1:6474/", "external"),
    ("javascript:alert(1)", "reject"), ("file:///etc/passwd", "reject"), ("data:text/html,hello", "reject"),
])
def test_native_navigation_keeps_the_private_origin(url, kind):
    assert macwindow.navigation_kind("http://127.0.0.1:6474/", url) == kind


def test_only_local_exports_can_download():
    origin = "http://127.0.0.1:6474/"
    assert macwindow.export_url(origin, origin + "api/export")
    assert macwindow.export_url(origin, "blob:" + origin + "uuid")
    assert not macwindow.export_url(origin, "blob:http://127.0.0.1:64740/uuid")
    assert not macwindow.export_url(origin, "https://other.example/export")


def test_missing_pyobjc_still_shows_a_finder_launch_error(monkeypatch):
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "AppKit", None)
    calls = []
    monkeypatch.setattr(window.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    window.tell_desktop("decompdecomp can't open", "Install PyObjC")
    assert calls and calls[0][0] == "/usr/bin/osascript"
    assert calls[0][-2:] == ["decompdecomp can't open", "Install PyObjC"]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS ps process metadata")
def test_mac_finds_detached_helpers_even_with_spaces_in_home(tmp_path):
    manager = object.__new__(app_agents.Manager)
    manager.home = tmp_path / "space home"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            env=dict(os.environ, **{app_agents.HELPER_ENV: str(manager.home)}), start_new_session=True)
    try:
        assert app_agents._proc_start(proc.pid) is not None
        assert os.getpgid(proc.pid) in manager._strays()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("cpu", ["x86_64", "arm64"])
def test_mac_hosts_use_pinned_tools(cpu, monkeypatch):
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-" + cpu)
    monkeypatch.setattr(toolchains.wsl, "wanted", lambda: False)
    assert toolchains.unsupported_cpu() is None
    assert toolchains.get("gcc-2.7.2-kmc").supported
    assert not toolchains.get("ee-gcc-2.96").supported
    assert all(p["sha256"] and p.get("rosetta") for p in toolchains.get("gcc-2.7.2-kmc").parts_for())


def test_missing_rosetta_is_an_actionable_error_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    downloads = []
    monkeypatch.setattr(toolchains, "_download", lambda *a, **kw: downloads.append(a))
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(toolchains.ToolchainError, match="softwareupdate --install-rosetta"):
        toolchains.install("gcc-2.7.2-kmc")
    assert not list(tmp_path.iterdir())
    assert not downloads
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    (tmp_path / "gcc-2.7.2-kmc").mkdir()
    (tmp_path / "gcc-2.7.2-kmc" / toolchains.MANIFEST).write_text("{}")
    assert toolchains.require("gcc-2.7.2-kmc") == tmp_path / "gcc-2.7.2-kmc"
    toolchains._intel_mac_runtime_ready.cache_clear()


def test_finder_launcher_preserves_interpreter_and_helper_path(monkeypatch, tmp_path):
    home = tmp_path / "Owner's Mac"
    monkeypatch.setattr(launcher.Path, "home", lambda: home)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.sys, "executable", str(home / "Python env/bin/python"))
    monkeypatch.setattr(launcher.edition, "DEV", False)
    monkeypatch.setenv("PATH", str(home / "node bin") + ":/usr/bin:/bin")
    launcher.install_launcher()
    bundle = home / "Applications/decompdecomp.app/Contents"
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "com.ndelic.decompdecomp"
    script = bundle / "MacOS" / info["CFBundleExecutable"]
    if os.name != "nt":
        assert script.stat().st_mode & 0o111
    lines = script.read_text().splitlines()
    assert shlex.split(lines[1]) == ["export", "PATH=" + os.environ["PATH"]]
    assert shlex.split(lines[2]) == ["exec", sys.executable, "-m", "decompnet.app.cli", "$@"]


def test_mac_chooser_uses_the_apps_python_and_cancellation_is_empty(monkeypatch):
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="/Users/owner/Decomp save\n")
    monkeypatch.setattr(picker.subprocess, "run", run)
    assert picker.choose("folder", "Open a save") == "/Users/owner/Decomp save"
    assert calls[0][:2] == [sys.executable, "-I"]
    monkeypatch.setattr(picker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=""))
    assert picker.choose("file", "Open") == ""


def test_mac_opens_folders_in_finder(monkeypatch, tmp_path):
    monkeypatch.setattr(hostenv.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(hostenv.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))
    assert hostenv.open_folder(tmp_path) is None
    assert calls == [["open", str(tmp_path)]]


def test_mac_window_needs_no_x11_display(monkeypatch):
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object())
    window.missing.cache_clear()
    try:
        assert window.missing() is None
        monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: None)
        window.missing.cache_clear()
        assert "PyObjC" in window.missing()
    finally:
        window.missing.cache_clear()


def test_mac_window_uses_its_python_and_private_environment(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.setattr(window, "missing", functools.cache(lambda: None))
    monkeypatch.setenv("DECOMP_WINDOW", "1")
    monkeypatch.setattr(window.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(window.subprocess, "Popen", popen)
    assert window.Window(tmp_path, "http://127.0.0.1:6474/", "private-token", lambda: None).open()
    argv, kwargs = calls[0]
    assert argv[:3] == [sys.executable, "-I", str(window.MAC_WINDOW_PROGRAM)]
    assert "private-token" not in " ".join(argv)
    assert kwargs["env"]["DECOMP_WINDOW_TOKEN"] == "private-token"


@pytest.mark.skipif(os.name == "nt", reason="the installer is a POSIX shell script")
def test_mac_installer_skips_linux_packages_and_tracks_upstream(tmp_path):
    bindir = tmp_path / "test bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    scripts = {
        "uname": 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
        "uv": 'case "$*" in --version) echo "uv 0.13.5";; "tool dir --bin") echo ' + shlex.quote(str(bindir)) + ';; esac',
        "curl": 'echo \'{"sha":"' + "a" * 40 + '"}\'',
        "decomp": 'printf "%s\\n" "$*" >> ' + shlex.quote(str(calls)),
    }
    for name, text in scripts.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + text + "\n")
        path.chmod(0o755)
    script = tmp_path / "install.sh"
    script.write_bytes((Path(__file__).resolve().parents[1] / "site/public/install.sh").read_bytes())
    env = dict(os.environ, HOME=str(tmp_path / "home"), PATH=str(bindir) + ":/usr/bin:/bin")
    result = subprocess.run(["bash", str(script), "--no-start"],
                            capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "--repo n-delic/decompdecomp" in recorded and "--branch main" in recorded
    assert "setup" in recorded and "shortcut" in recorded
    assert "GTK" not in result.stdout and "sudo" not in result.stdout


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("DECOMP_TEST_COCOA") != "1",
                    reason="set DECOMP_TEST_COCOA=1 on a Mac desktop for the native WebKit smoke test")
def test_native_webkit_loads_the_app_and_obeys_close_confirmation():
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("native_macwindow.py"))],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native macOS window passed" in result.stdout


@pytest.mark.parametrize("kind,accepted", [("folder", True), ("file", True), ("file", False)])
def test_native_picker_selects_files_or_folders_and_honors_cancel(monkeypatch, capsys, kind, accepted):
    settings = {}
    panel = SimpleNamespace(
        setTitle_=lambda value: settings.update(title=value),
        setCanChooseDirectories_=lambda value: settings.update(directories=value),
        setCanChooseFiles_=lambda value: settings.update(files=value),
        setAllowsMultipleSelection_=lambda value: settings.update(multiple=value),
        runModal=lambda: 1 if accepted else 0,
        URL=lambda: SimpleNamespace(path=lambda: "/Users/owner/My save"))
    app = SimpleNamespace(setActivationPolicy_=lambda _: None, activateIgnoringOtherApps_=lambda _: None)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSApplication=SimpleNamespace(sharedApplication=lambda: app), NSModalResponseOK=1,
        NSOpenPanel=SimpleNamespace(openPanel=lambda: panel)))
    monkeypatch.setattr(picker.sys, "platform", "darwin")
    picker.main(kind, "Open a save")
    assert capsys.readouterr().out == ("/Users/owner/My save\n" if accepted else "")
    assert settings == {"title": "Open a save", "directories": kind == "folder", "files": kind == "file", "multiple": False}


def test_finder_can_find_homebrew_uv_without_a_shell_profile(monkeypatch):
    monkeypatch.setattr(hostenv.sys, "platform", "darwin")
    monkeypatch.setattr(hostenv.shutil, "which", lambda _: None)
    monkeypatch.setattr(hostenv.Path, "is_file", lambda path: path.as_posix() == "/opt/homebrew/bin/uv")
    assert Path(hostenv.find_uv()).as_posix() == "/opt/homebrew/bin/uv"


def test_demo_prerequisites_distinguish_path_tools_from_unavailable_compilers(monkeypatch, tmp_path):
    from . import test_consoles

    monkeypatch.setattr(test_consoles, "ZLIB", tmp_path)
    monkeypatch.setattr(toolchains, "installed", lambda tid: {} if tid == "gcc-2.7.2-kmc" else None)
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/tools/" + name)
    demo = SimpleNamespace(demo_tools=("gcc-2.7.2-kmc", "ld.lld", "llvm-objcopy"))
    assert test_consoles._missing(demo) == []
    demo.demo_tools = ("ee-gcc-2.96",)
    assert test_consoles._missing(demo) == ["ee-gcc-2.96"]
