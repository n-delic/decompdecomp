"""Windows installs, command lines and process lifetimes, without any real agent or user project."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from decompnet import agents, hostenv, toolchains
from decompnet.app import tray, updates


def test_windows_tools_and_caches_live_in_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchains.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert toolchains.data_dir() == tmp_path / "decompnet"
    assert toolchains.venv_bin(tmp_path, "python") == tmp_path / "Scripts" / "python.exe"


def test_desktop_launches_find_uv_outside_path(monkeypatch, tmp_path):
    monkeypatch.setattr(hostenv.sys, "platform", "win32")
    monkeypatch.setattr(hostenv.shutil, "which", lambda _: None)
    monkeypatch.setattr(hostenv.Path, "home", lambda: tmp_path)
    uv = tmp_path / ".local" / "bin" / "uv.exe"
    uv.parent.mkdir(parents=True)
    uv.write_bytes(b"stand-in")
    assert hostenv.find_uv() == str(uv)
    assert updates.uv() == str(uv)


def test_windows_agent_discovery_finds_npm_shims(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("needs Windows executable lookup")
    from decompnet.app import agents as helpers

    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    shim = tmp_path / "npm" / "test-agent.cmd"
    shim.parent.mkdir()
    shim.write_text("@echo off\necho version\n")
    assert Path(helpers._which("test-agent")) == shim


@pytest.mark.parametrize("filename", ["agent.js", "agent"])
def test_npm_command_lines_preserve_quotes_and_shell_symbols(monkeypatch, tmp_path, filename):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node to exercise an npm shim")
    monkeypatch.setattr(agents.sys, "platform", "win32")
    directory = tmp_path / "Tools with spaces ü"
    directory.mkdir()
    shim = directory / "agent.cmd"
    script = directory / filename
    shim.write_text(f'@echo off\nSET "_prog=node"\n"%_prog%" "%dp0%\\{filename}" %*\n')
    script.write_text("console.log(JSON.stringify(process.argv.slice(2)))", encoding="utf-8")
    args = ['key={ Authorization="Bearer secret" }', 'x & y | z ^ %PATH%', 'two\nlines', "日本語"]
    argv = agents.cli_argv([str(shim), *args])
    assert argv[0] == node and argv[1] == str(script)
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0 and json.loads(result.stdout) == args


def test_windows_tools_have_native_pinned_downloads():
    for name in ("objdiff-cli", "dtk", "cloudflared", "binutils-mips", "gc-wii-binutils", "ido-7.1", "ido-5.3"):
        parts = toolchains.get(name).parts_for("windows-x86_64")
        assert parts and all(len(part["sha256"]) == 64 for part in parts), name


def test_windows_tray_relays_actions_without_starting_a_desktop(monkeypatch):
    import io
    from types import SimpleNamespace

    calls, launched = [], []
    proc = SimpleNamespace(stdout=io.StringIO("ready\nopen\nquit\n"), wait=lambda: 0)
    monkeypatch.setattr(tray.subprocess, "Popen", lambda *args, **kwargs: launched.append((args, kwargs)) or proc)
    assert tray._start_windows(lambda: calls.append("open"), lambda: calls.append("quit"))
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(calls) == ["open", "quit"]
    assert Path(launched[0][0][0][2]).name == "wintray.py"


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows kernel jobs")
def test_a_windows_job_ends_children_after_their_parent_exits(tmp_path):
    from decompnet.winprocess import Job

    from .test_platform import _alive

    code = ("import subprocess, sys\n"
            "sys.stdin.readline()\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(p.pid, flush=True)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    job = Job(proc)
    child = None
    try:
        proc.stdin.write("go\n")
        proc.stdin.flush()
        child = int(proc.stdout.readline())
        proc.wait(10)
        assert _alive(child)
    finally:
        job.close()
        proc.wait(10)
        proc.stdin.close()
        proc.stdout.close()
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _alive(child)


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows helper gate")
def test_windows_helpers_receive_large_multiline_prompts_without_a_shell():
    from decompnet.app import agents as helpers

    gate = Path(helpers.__file__).with_name("winhelper.py")
    code = "import sys; print(sys.stdin.read(), end='')"
    prompt = 'line one & < > | ^ %PATH% "quotes"\n日本語\n' * 1000
    result = subprocess.run([sys.executable, str(gate), sys.executable, "-X", "utf8", "-c", code],
                            input="\x01" + prompt, capture_output=True, text=True, encoding="utf-8", timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0 and result.stdout == prompt


@pytest.mark.skipif(sys.platform != "win32", reason="needs a Windows console")
def test_compiler_commands_do_not_flash_a_console(tmp_path):
    from decompnet.compiler import CompilerConfig, _exec

    result = _exec(CompilerConfig(cmd=[], sandbox="none"), tmp_path,
                   [sys.executable, "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"], 15)
    assert result.returncode == 0 and result.stdout.strip() == "0"


def test_project_builds_use_the_apps_python_for_python3(tmp_path, monkeypatch):
    from decompnet.forks.project import ForkProject

    monkeypatch.setenv("PATH", "")
    ok, output = ForkProject(tmp_path).run(["python3", "-c", "import sys; print(sys.executable)"], log=lambda _: None)
    assert ok and Path(output) == Path(sys.executable)


def test_an_explicit_timezone_reset_does_not_use_the_windows_local_zone():
    from datetime import datetime, timezone

    from decompnet.app.agents import limit_reset

    now = datetime(2026, 10, 18, 13, tzinfo=timezone.utc).timestamp()
    reset = limit_reset("usage limit resets 3pm (America/New_York)", now)
    assert reset == datetime(2026, 10, 18, 19, tzinfo=timezone.utc).timestamp()


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows process cleanup")
def test_stopping_an_exited_process_never_targets_its_pid_again(monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import agents as helpers

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    commands = []
    monkeypatch.setattr(helpers.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(helpers.subprocess, "run", lambda *args, **kwargs: commands.append(args))
    helpers._terminate(SimpleNamespace(pid=2**31 - 1, poll=lambda: 0))
    assert commands == []
