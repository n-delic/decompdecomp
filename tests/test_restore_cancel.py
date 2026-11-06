"""Cancellation reaches compiler waits and imports, before another saved function can be accepted."""

import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from decompnet import compiler, wsl
from decompnet.app import saves


def test_a_running_compiler_stops_without_waiting_for_its_timeout(tmp_path, monkeypatch):
    started = threading.Event()
    processes = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        started.set()
        return proc

    monkeypatch.setattr(compiler.subprocess, "Popen", launch)

    def cancel():
        if started.is_set():
            raise RuntimeError("cancelled during compile")

    before = time.monotonic()
    with compiler.cancellable(cancel), pytest.raises(RuntimeError, match="cancelled during compile"):
        compiler._exec(compiler.CompilerConfig([], sandbox="none"), tmp_path,
                       [sys.executable, "-c", "import time; time.sleep(60)"], 60)
    assert time.monotonic() - before < 5
    assert processes[0].poll() is not None


def test_restore_import_uses_the_same_cancellation_scope_as_judging(tmp_path, monkeypatch):
    (tmp_path / "include").mkdir()
    (tmp_path / "include/common.h").write_text("/* shared header */\n")
    (tmp_path / saves.MANIFEST).write_text(json.dumps({
        "format": saves.FORMAT, "game": {"sha1": "same"}, "headers": {"": "common.h"},
        "layout": [], "functions": {},
    }))
    monkeypatch.setattr(saves.consoles, "identify", lambda _: SimpleNamespace(
        read_game=lambda *_: SimpleNamespace(info={"sha1": "same"})))
    checks = []

    def log(*_):
        pass

    log.check_cancel = lambda: checks.append(True)

    def importing(*args, **kwargs):
        assert compiler._CANCEL_CHECK.get() is log.check_cancel
        raise RuntimeError("cancelled at import")

    with pytest.raises(RuntimeError, match="cancelled at import"):
        net = SimpleNamespace(import_rom=importing, store=SimpleNamespace(all=lambda *_: []))
        saves.restore(net, str(tmp_path), tmp_path / "game", log)
    assert checks
    assert compiler._CANCEL_CHECK.get() is None


def test_cancellation_does_not_wait_for_a_free_compiler_slot(tmp_path, monkeypatch):
    class Busy:
        def acquire(self, timeout):
            return False

        def release(self):
            pytest.fail("released another job's slot")

    monkeypatch.setattr(compiler, "_SLOTS", Busy())

    def cancel():
        raise RuntimeError("cancelled while waiting")

    with compiler.cancellable(cancel), pytest.raises(RuntimeError, match="cancelled while waiting"):
        compiler.compile_source(compiler.CompilerConfig(["unused"], sandbox="none"), "", "void f(void) {}")


def test_wsl_cancellation_signals_linux_before_removing_its_script(tmp_path, monkeypatch):
    script = tmp_path / "compile.sh"
    script.write_text("sleep 60")
    marker = tmp_path / "compile.sh.cancel"
    monkeypatch.setattr(wsl, "path", str)
    monkeypatch.setattr(wsl, "exe", lambda: "wsl")
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("cancelled")

    class Process:
        def __init__(self, args, **kwargs):
            self.args = args
            assert "setsid sh" in args[-1] and '/bin/kill -KILL -- -"$child"' in args[-1]

        def communicate(self, timeout):
            if not marker.exists():
                raise subprocess.TimeoutExpired(self.args, timeout)
            return b"", b""

    monkeypatch.setattr(wsl.subprocess, "Popen", Process)
    with pytest.raises(RuntimeError, match="cancelled"):
        wsl._cancellable_script(str(script), 60, 0, check)
    assert not marker.exists()


def test_wsl_compiler_receives_cancellation_check(tmp_path, monkeypatch):
    def check():
        pass

    def run(*args, **kwargs):
        assert kwargs["check_cancel"] is check
        return "stopped"

    monkeypatch.setattr(wsl, "run", run)
    with compiler.cancellable(check):
        assert compiler._exec(compiler.CompilerConfig(["unused"], wsl=True), tmp_path, [], 60) == "stopped"
