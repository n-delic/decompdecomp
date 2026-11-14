"""compare_behaviour (app/behaviour.py): a function's recorded calls through its recompiled and decompiled C, and
what differs; the comparison and the background run, on stand-ins (the whole run needs a game: CI has none)."""
import threading
import time

import pytest

from decompnet.app import behaviour
from decompnet.app.tools import tool_names
from decompnet.consoles.n64 import emulator


def _mem(words: dict[int, int]) -> bytes:
    m = bytearray(0x200000)
    for a, v in words.items():
        m[a & 0x1FFFFFFF:(a & 0x1FFFFFFF) + 4] = v.to_bytes(4, "big")
    return bytes(m)


def test_results_and_memory_outside_the_stack_are_compared(tmp_path):
    sp = 0x80100000
    (tmp_path / "r.rdram").write_bytes(_mem({0x80050000: 0xBF800000, sp - 0x20: 1}))
    (tmp_path / "p.rdram").write_bytes(_mem({0x80050000: 0x3F800000}))
    recomp = {"v0": 0x800, "v1": 0, "f0": 0, "rdram": tmp_path / "r.rdram"}
    port = {"v0": 0, "v1": 0, "f0": 0, "rdram": tmp_path / "p.rdram"}
    found = behaviour.compare_calls(_mem({}), recomp, port, sp, lambda a: "gCarMatrix" if a == 0x80050000 else hex(a))
    assert found == ["$v0: recompiled 00000800, decompiled 00000000",
                     "memory at gCarMatrix (1 words to 80050004): recompiled BF800000, decompiled 3F800000, "
                     "before the call 00000000"], "the callee's frames below $sp aren't a difference"
    assert behaviour.compare_calls(_mem({}), {**recomp, "v0": 0, "rdram": tmp_path / "p.rdram"}, port, sp, hex) == []


def test_a_long_comparison_answers_still_running_then_its_result(monkeypatch):
    gate = threading.Event()
    runs = []

    def compare(*a):
        runs.append(a)
        gate.wait(5)
        return "2 of 2 calls behave the same."
    monkeypatch.setattr(behaviour, "_runs", {})
    monkeypatch.setattr(behaviour, "compare", compare)
    monkeypatch.setattr(behaviour, "WAIT", 0.1)
    assert behaviour.answer(None, "game", "f").startswith("Still running")
    gate.set()
    time.sleep(0.2)
    assert behaviour.answer(None, "game", "f") == "2 of 2 calls behave the same." and len(runs) == 1
    with pytest.raises(Exception, match="vis is 1 to"):
        behaviour.answer(None, "game", "f", vis=0)


def test_a_failed_run_is_said_and_the_next_call_starts_again(monkeypatch):
    calls = []

    def fails():
        calls.append(1)
        raise RuntimeError("no ROM")
    monkeypatch.setattr(behaviour, "_runs", {})
    for _ in range(2):
        with pytest.raises(RuntimeError, match="no ROM"):
            behaviour.later(("k",), fails, wait=5)
    assert len(calls) == 2


def test_the_emulator_records_calls_and_rebuilds_its_frontend_when_its_source_changes(tmp_path, monkeypatch):
    run = emulator.parse("recorded 2452 0 /tmp/rec/0\ndumped 2452 /tmp/rec/0.rdram\nstopped 2460\n")
    assert run.recorded == [emulator.Path("/tmp/rec/0")] and run.vis == 2460
    built, installed = [], []
    monkeypatch.setattr(emulator, "_programs", lambda dest, run=None: built.append(dest))
    from decompnet import toolchains
    monkeypatch.setattr(toolchains, "install", lambda tid, **kw: installed.append(tid) or tmp_path)
    (tmp_path / "programs.sha256").write_text(emulator._stamp())
    assert emulator.current(tmp_path, log=lambda *a: None) == tmp_path and not built and not installed
    (tmp_path / "programs.sha256").write_text("older")
    emulator.current(tmp_path, log=lambda *a: None)
    assert installed == ["mupenre"], "an install without the headers to build them is installed again"
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "m64p_types.h").write_text("")
    emulator.current(tmp_path, log=lambda *a: None)
    assert built == [tmp_path]


def test_rom_projects_get_the_tool():
    assert "compare_behaviour" in tool_names(False) and "compare_behaviour" not in tool_names(True)
