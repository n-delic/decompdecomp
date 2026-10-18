"""What the app needs from the system around it: agent commands as they are, stopping an agent with everything it
started, UTF-8 output, the guides intact, and the build tools' paths (Windows as a host was removed in the rewrite;
Windows games stay)."""

import os
import shutil
import subprocess
import sys
import time

import pytest

from decompnet.app import agents
from decompnet.forks.project import ForkProject


def _alive(pid: int) -> bool:
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
    proc = subprocess.Popen([sys.executable, "-c", code],
                            start_new_session=True)
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

    text = tools.guide_text("fork")
    assert "…" in text and "â€" not in text


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
