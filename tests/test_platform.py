"""decompdecomp on Windows as on Linux: stopping an agent with everything it started, UTF-8 output and files, HTTPS,
startup apps, dtk's build paths."""

import os
import shutil
import subprocess
import sys
import time

import pytest

from decompnet.app import agents, service
from decompnet.forks.project import ForkProject

WIN = sys.platform == "win32"
windows = pytest.mark.skipif(not WIN, reason="Windows only")


def _alive(pid: int) -> bool:
    if WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_stopping_an_agent_stops_what_it_started(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0,
                            start_new_session=not WIN)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    child = int(pidfile.read_text())
    agents._terminate(proc)
    proc.wait(10)
    for _ in range(50):
        if not _alive(child):
            break
        time.sleep(0.1)
    assert not _alive(child)


def test_output_into_a_pipe_is_utf8():
    res = subprocess.run([sys.executable, "-I", "-c", "import decompnet; decompnet.utf8_output(); print('→ ✓ 🎉')"],
                         capture_output=True)
    assert res.returncode == 0 and res.stdout.decode("utf-8").strip() == "→ ✓ 🎉"


def test_the_guides_reach_agents_intact():
    from decompnet.app import tools

    text = tools._guide("fork")
    assert "…" in text and "â€" not in text


@windows
def test_https_is_verified_by_windows():
    import ssl

    import decompnet  # noqa: F401

    assert ssl.SSLContext.__module__.startswith("truststore")


@windows
def test_running_at_log_on_needs_no_administrator(monkeypatch):
    import winreg

    key = r"Software\decompdecomp-tests\Run"
    monkeypatch.setattr(service, "RUN_KEY", key)
    started = []
    monkeypatch.setattr(service.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1))
    try:
        assert "startup apps" in service.install() and service.installed()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value = winreg.QueryValueEx(k, service.RUN_NAME)[0]
        assert "pythonw.exe" in value.lower() and "decompnet.app.cli" in value and started
        service.remove()
        assert not service.installed()
    finally:
        for k in (key, r"Software\decompdecomp-tests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass


def test_builds_find_decomp_wtfs_own_ninja(tmp_path, monkeypatch):
    """ninja comes in decompdecomp's own environment, which isn't on PATH once decompdecomp is installed (a GameCube game's
    first build ran `ninja`: on Windows it wasn't found)."""
    own = os.path.normcase(os.path.dirname(sys.executable))
    monkeypatch.setenv("PATH", os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                               if os.path.normcase(p.rstrip("\\/")) != own))
    if shutil.which("ninja"):
        pytest.skip("another ninja is on PATH")
    ok, out = ForkProject(tmp_path).run(["ninja", "--version"], log=lambda *_: None)
    assert ok, out


@pytest.mark.parametrize("target", ["build/G1E751/ok", r"build\G1E751\ok"])
def test_the_checksum_target_is_found_with_either_slash(tmp_path, target):
    (tmp_path / "build.ninja").write_text(f"build {target}: check config/G1E751/build.sha1\n", encoding="utf-8")
    assert ForkProject(tmp_path).check_target() == target
