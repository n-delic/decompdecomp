"""Limits on the tools decompdecomp runs (limits.py)."""

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time

import pytest

from decompnet import compiler, limits
from decompnet.forks import project
from decompnet.forks.project import ForkProject


@pytest.mark.skipif(sys.platform == "win32", reason="no core dumps on Windows")
def test_the_tools_the_app_starts_dump_no_core():
    """A compiler aborting under wibo put up the desktop's crash dialog: what the app starts inherits no core dumps."""
    code = "from decompnet import limits; import subprocess; limits.no_core_dumps(); " \
           "print(subprocess.run(['sh', '-c', 'ulimit -c'], capture_output=True, text=True).stdout.strip())"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "0"


def test_open_file_limit_is_raised_without_changing_hard_limit(monkeypatch):
    resource=pytest.importorskip('resource')
    from decompnet import limits
    current=[256,65536];seen=[]
    monkeypatch.setattr(resource,'getrlimit',lambda kind:tuple(current))
    def setlimit(kind,value):seen.append(value);current[:]=value
    monkeypatch.setattr(resource,'setrlimit',setlimit)
    assert limits.raise_open_file_limit()==8192
    assert seen==[(8192,65536)]
    assert limits.raise_open_file_limit()==8192 and len(seen)==1
    current[:]=[256,1024]
    assert limits.raise_open_file_limit()==1024 and seen[-1]==(1024,1024)


def test_open_file_limit_failure_is_nonfatal_and_high_limits_are_preserved(monkeypatch):
    resource=pytest.importorskip('resource')
    from decompnet import limits
    monkeypatch.setattr(resource,'getrlimit',lambda kind:(256,resource.RLIM_INFINITY))
    def denied(*args):raise OSError('not permitted')
    monkeypatch.setattr(resource,'setrlimit',denied)
    assert limits.raise_open_file_limit() is None
    monkeypatch.setattr(resource,'getrlimit',lambda kind:(65536,65536))
    assert limits.raise_open_file_limit()==65536


@pytest.mark.skipif(sys.platform == 'win32',reason='POSIX descriptor limits')
def test_raised_limit_is_inherited_by_child_tools():
    code='''import resource,subprocess,sys
from decompnet import limits
soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
if hard!=resource.RLIM_INFINITY and hard<8192:sys.exit(77)
resource.setrlimit(resource.RLIMIT_NOFILE,(256,hard))
assert limits.raise_open_file_limit()==8192
print(subprocess.check_output([sys.executable,'-c','import resource;print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])'],text=True).strip())
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    if result.returncode==77:pytest.skip('host hard limit below 8192')
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='8192'


# The tests below wait for processes to start and to end, not for fixed times, with time limits far longer than a
# Python takes to start, so a slow laptop doesn't fail them (#228's review).
START_SECONDS = 3  # a build's or compile's limit, in tests where it's meant to run out
GONE_SECONDS = 15  # how long a stopped process may take to go


def _tree(tmp_path, *, parent_waits: bool = True, child_group: bool = False, child_session: bool = False) -> list[str]:
    """A command that starts a child, which writes its pid and runs for a minute, and then waits for good (or
    `parent_waits` False: ends once the child is up, leaving it holding the output). `child_group`: the child goes
    into a process group of its own (as ninja's commands do), `child_session`: a session of its own (setsid)."""
    pid_file = tmp_path / "child.pid"
    child = ("import os, pathlib, time\n"
             + ("os.setpgrp()\n" if child_group else "")
             + f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    parent = (f"import pathlib, subprocess, sys, time\n"
              f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session={child_session})\n"
              f"while not pathlib.Path({str(pid_file)!r}).exists(): time.sleep(0.02)\n"
              + ("time.sleep(600)\n" if parent_waits else "print('done', flush=True)\n"))
    script = tmp_path / "parent.py"  # (a file: a ninja command is one line)
    script.write_text(parent)
    return [sys.executable, str(script)]


def _child(tmp_path) -> int:
    return int((tmp_path / "child.pid").read_text())


def _running(pid: int) -> bool:
    if os.name == "nt":  # (os.kill would end it: Windows has no signal 0)
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " in listed
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _gone(pid: int) -> bool:
    """The process ends within GONE_SECONDS (a stopped orphan is gone once its new parent reaps it)."""
    deadline = time.monotonic() + GONE_SECONDS
    while _running(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_a_compile_out_of_time_stops_what_the_compiler_started(tmp_path):
    cfg = compiler.CompilerConfig(cmd=[], sandbox="none")
    with pytest.raises(subprocess.TimeoutExpired):
        compiler._exec(cfg, tmp_path, _tree(tmp_path), timeout=START_SECONDS)
    assert _gone(_child(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="process groups and sessions are POSIX; Windows uses the Job Object")
def test_a_compile_out_of_time_stops_a_child_in_its_own_group_whose_compiler_has_gone(tmp_path):
    """Found only by its session, read with getsid: macOS's `ps -o sess` says 0 for every process, so a compiler
    child in a group of its own, its parent gone, outlived the compile."""
    cfg = compiler.CompilerConfig(cmd=[], sandbox="none")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        compiler._exec(cfg, tmp_path, _tree(tmp_path, parent_waits=False, child_group=True), timeout=START_SECONDS)
    assert _gone(_child(tmp_path))
    assert time.monotonic() - started < 30  # (stopped, not waited out: the child runs for a minute)


@pytest.mark.skipif(os.name == "nt", reason="setsid is POSIX")
def test_a_compile_out_of_time_returns_though_a_detached_child_holds_its_output(tmp_path):
    """A child that left the compiler's session isn't stopped with it; holding the output, it kept the stopped
    compile waiting (and its compiler slot taken) for as long as it ran."""
    cfg = compiler.CompilerConfig(cmd=[], sandbox="none")
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            compiler._exec(cfg, tmp_path, _tree(tmp_path, child_session=True), timeout=START_SECONDS)
        assert time.monotonic() - started < START_SECONDS + 8 + limits.STOP_GRACE_SECONDS
    finally:
        os.kill(_child(tmp_path), 9)


def test_a_successful_compile_skips_process_tree_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "stop_group", lambda *_: pytest.fail("success must not scan or stop process groups"))
    cfg = compiler.CompilerConfig(cmd=[], sandbox="none")
    result = compiler._exec(cfg, tmp_path, [sys.executable, "-c", 'print("ok")'], timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "ok"


def test_a_project_build_out_of_time_stops_what_it_started(tmp_path):
    ok, said = ForkProject(tmp_path).run(_tree(tmp_path), timeout=START_SECONDS)
    assert not ok and "didn't finish in" in said
    assert _gone(_child(tmp_path))


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("ninja"), reason="needs POSIX ninja")
def test_a_ninja_build_out_of_time_stops_a_command_in_another_process_group(tmp_path):
    (tmp_path / "build.ninja").write_text(f"rule tree\n  command = {shlex.join(_tree(tmp_path))}\nbuild result: tree\n")
    ok, said = ForkProject(tmp_path).run(["ninja", "result"], timeout=START_SECONDS)
    assert not ok and "didn't finish in" in said
    assert _gone(_child(tmp_path))


@pytest.mark.parametrize("child_group", [False, True] if os.name != "nt" else [False])
def test_a_build_that_ends_while_its_child_holds_the_output_keeps_its_result(tmp_path, monkeypatch, child_group):
    """The step succeeded, so the build did: it used to be waited on for the rest of its time limit (an hour) and
    then failed as out of time. What it left holding the output is stopped, on Windows too (the build's Job Object:
    taskkill /T found nothing under a step that had gone)."""
    monkeypatch.setattr(project, "OUTPUT_SECONDS", 1)
    started = time.monotonic()
    ok, said = ForkProject(tmp_path).run(_tree(tmp_path, parent_waits=False, child_group=child_group), timeout=600)
    assert ok and "done" in said
    assert time.monotonic() - started < 60
    assert _gone(_child(tmp_path))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses a Job Object")
def test_a_group_signal_refused_falls_back_to_each_process(tmp_path, monkeypatch):
    """Intel macOS refused os.killpg with PermissionError during cleanup."""
    monkeypatch.setattr(limits.os, "killpg", lambda *_: (_ for _ in ()).throw(PermissionError()))
    proc = subprocess.Popen(_tree(tmp_path), start_new_session=True)
    deadline = time.monotonic() + GONE_SECONDS
    while not (tmp_path / "child.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    limits.stop_group(proc)
    proc.wait(timeout=GONE_SECONDS)
    assert _gone(_child(tmp_path))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions")
def test_a_remembered_process_no_longer_in_the_session_is_left_alone(tmp_path):
    """A process number the watchdog saw may have gone to another program by the time a build is stopped."""
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        limits.stop_group(proc, {stranger.pid})
        proc.wait(timeout=GONE_SECONDS)
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()


def test_compiler_limits_are_configurable(monkeypatch):
    monkeypatch.setenv("DECOMP_COMPILER_MEMORY_MB", "64")
    monkeypatch.setenv("DECOMP_COMPILER_TIMEOUT_SECONDS", "12")
    assert limits.compiler_memory_limit() == 64 << 20
    assert limits.compiler_time_limit() == 12


def test_the_memory_watchdog_only_runs_on_macos(monkeypatch):
    """Elsewhere prlimit and systemd cap memory, and Windows has no ps to read it with."""
    monkeypatch.setattr(limits.sys, "platform", "win32")
    with limits.CompilerMemoryWatchdog(subprocess.Popen([sys.executable, "-c", "pass"])) as watchdog:
        assert not watchdog._watched and watchdog not in limits._MONITOR.watches
        watchdog.proc.wait()


def test_one_monitor_thread_watches_every_compiler(monkeypatch):
    """Not a thread per compiler process: a deep pass starts hundreds a second."""
    monkeypatch.setattr(limits.sys, "platform", "darwin")
    before = {t.ident for t in threading.enumerate() if t.name == "compiler-memory-watchdog"}
    procs = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.6)"]) for _ in range(5)]
    watches = [limits.CompilerMemoryWatchdog(proc, limit=1 << 40) for proc in procs]
    for watch in watches:
        watch.__enter__()
    try:
        names = [t for t in threading.enumerate() if t.name == "compiler-memory-watchdog"]
        assert len(names) <= max(1, len(before))
        assert all(w in limits._MONITOR.watches for w in watches)
    finally:
        for proc, watch in zip(procs, watches):
            proc.wait()
            watch.__exit__(None, None, None)
    assert not any(w in limits._MONITOR.watches for w in watches) and not any(w.exceeded for w in watches)


@pytest.mark.skipif(sys.platform != "darwin", reason="the memory watchdog is macOS's")
def test_a_mac_compiler_over_its_memory_limit_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMP_COMPILER_MEMORY_MB", "48")
    cfg = compiler.CompilerConfig(cmd=[], sandbox="none")
    result = compiler._exec(cfg, tmp_path, [sys.executable, "-c", "import time; data = b'x' * (96 << 20); "
                                            "time.sleep(60)"], timeout=60)
    assert result.returncode != 0 and "exceeded the 48 MiB memory limit" in result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="the memory watchdog is macOS's")
def test_a_mac_build_over_its_memory_limit_in_all_is_stopped(tmp_path, monkeypatch):
    """A build's limit is for all its processes together, as its systemd scope's is on Linux: two that are each
    under it are stopped once they're over it together."""
    monkeypatch.setattr(project, "default_cap", lambda: 96 << 20)
    hog = "import time; data = b'x' * (64 << 20); time.sleep(60)"
    build = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {hog!r}]); exec({hog!r})"
    started = time.monotonic()
    ok, said = ForkProject(tmp_path).run([sys.executable, "-c", build], timeout=60)
    assert not ok and "more than 96 MiB of memory in all" in said
    assert time.monotonic() - started < 30
