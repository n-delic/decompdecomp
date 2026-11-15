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
    recomp = {"v0": 0x800, "v1": 0, "f0": 0, "f1": 0, "rdram": tmp_path / "r.rdram"}
    port = {"v0": 0, "v1": 0, "f0": 0, "f1": 0, "rdram": tmp_path / "p.rdram"}
    found = behaviour.compare_calls(_mem({}), recomp, port, sp, lambda a: "gCarMatrix" if a == 0x80050000 else hex(a))
    assert found == ["$v0: recompiled 00000800, decompiled 00000000",
                     "memory at gCarMatrix (1 words to 80050004): recompiled BF800000, decompiled 3F800000, "
                     "before the call 00000000"], "the callee's frames below $sp aren't a difference"
    assert behaviour.compare_calls(_mem({}), {**recomp, "v0": 0, "rdram": tmp_path / "p.rdram"}, port, sp, hex) == []
    # only the registers that hold what the function returns: $v1 and $f0 are scratch after a word
    noisy = {**recomp, "v0": 0, "v1": 0x13, "f0": 5, "f1": 0, "rdram": tmp_path / "p.rdram"}
    word = {**port, "f1": 0, "kind": 1}
    assert behaviour.compare_calls(_mem({}), noisy, word, sp, hex) == []
    assert behaviour.compare_calls(_mem({}), noisy, {**word, "kind": 2}, sp, hex) == [
        "$v1: recompiled 00000013, decompiled 00000000"]
    # a void function's $v0 is a result only when a caller takes it (the suspicious calls' void-result)
    void = {**word, "kind": 0, "v0": 1}
    assert behaviour.compare_calls(_mem({}), noisy, void, sp, hex, v0_taken=False) == []
    assert behaviour.compare_calls(_mem({}), noisy, void, sp, hex, v0_taken=True) == [
        "$v0: recompiled 00000000, decompiled 00000001"]


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


@pytest.fixture
def game(tmp_path, monkeypatch):
    """A homemade project with three matched functions, the console's parts stood in for: the emulator records the
    calls of the ones in `reached`, the recompiled side gives $v0 7, the decompiled side 7 for `same` and 0 else."""
    import hashlib
    import json

    from decompnet.app.engine import RomNetwork
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    image = bytes(0x2000)
    (tmp_path / "game.z64").write_bytes(image)
    cfg = dict(kind="rom", platform="n64", rom=dict(file="game.z64", sha1=hashlib.sha1(image).hexdigest()),
               segments=[dict(name="main", rom_start=0x1000, rom_end=0x2000, vram=0x80000400)],
               compiler={"toolchain": "gcc-2.7.2-kmc", "flags": []})
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg))).lastrowid
        for name, vram in (("same", 0x80000400), ("differs", 0x80000500), ("unreached", 0x80000600)):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source,meta) "
                       "VALUES(?,?,?,?,?,?,?,?)", (pid, name, 16, 4, vram, "matched", f"void {name}(void) {{}}", "{}"))
    console = type(net.console(net.project_row("game")))
    runs = []

    class Emulator:
        def run(self, rom, vis, *, breaks, record, stop=lambda: False, on_record=None, pending=0, **kw):
            runs.append(list(breaks))
            assert pending > 0, "the frontend waits for the batch to take each recording"
            folder, out = emulator.Path(record[0]), emulator.Run(vis=vis)
            for vram in breaks:
                if vram != 0x80000600:
                    base = folder / f"{vram:08X}_0"
                    emulator.Path(f"{base}.state").write_text(
                        f"{vram:08X} " + " ".join("0" for _ in range(34)) + " " + " ".join("0" for _ in range(32)))
                    emulator.Path(f"{base}.rdram").write_bytes(bytes(0x1000))
                    out.recorded.append(base)
                    on_record(base)
            return out

    class Recompiler:
        @staticmethod
        def load(root):
            return None

        @staticmethod
        def run_one(program, vram, state, rdram, out):
            (emulator.Path(f"{out}.rdram")).write_bytes(bytes(0x1000))
            return {"ok": True, "v0": 7, "v1": 0, "f0": 0, "f1": 0, "rdram": emulator.Path(f"{out}.rdram"),
                    "runtime": []}

    def port_call(program, rom, vram, state, rdram, out, timeout=60):
        (emulator.Path(f"{out}.rdram")).write_bytes(bytes(0x1000))
        return {"ok": True, "v0": 7 if vram == 0x80000400 else 0, "v1": 0, "f0": 0, "f1": 0,
                "rdram": emulator.Path(f"{out}.rdram")}
    monkeypatch.setattr(console, "emulator", Emulator())
    monkeypatch.setattr(console, "recompiler", Recompiler())
    monkeypatch.setattr(behaviour, "_sides", lambda *a, **k: (None, "harness", "program", "rom"))
    monkeypatch.setattr(behaviour, "_port_call", port_call)
    yield net, tmp_path, runs
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_check_behaviour_runs_every_matched_function_and_keeps_its_results(game, monkeypatch):
    net, root, runs = game
    monkeypatch.setattr(behaviour, "CHUNK", 2)
    s = behaviour.batch(net, "game", vis=600, log=lambda *a: None)
    assert runs == [[0x80000400, 0x80000500], [0x80000600]], "recorded in chunks, by address"
    assert (s["same"], s["differs"], s["not_reached"], s["errors"]) == (1, 1, 1, 0)
    assert s["examples"] == ["differs: $v0: recompiled 00000007, decompiled 00000000"]
    assert not list((root / "build" / "behaviour").rglob("*.rdram")), "only the verdicts are kept"
    # again: nothing new, nothing run; a function whose C changed is checked again
    behaviour.batch(net, "game", vis=600, log=lambda *a: None)
    assert len(runs) == 2
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='void differs(void) { return; }' WHERE name='differs'")
    behaviour.batch(net, "game", vis=600, log=lambda *a: None)
    assert runs[-1] == [0x80000500]
    # other settings: everything again
    behaviour.batch(net, "game", vis=900, log=lambda *a: None)
    assert runs[-2:] == [[0x80000400, 0x80000500], [0x80000600]]


def test_stop_keeps_what_was_checked(game, monkeypatch):
    net, root, runs = game
    monkeypatch.setattr(behaviour, "CHUNK", 1)

    class Log:
        cancel_event = threading.Event()

        def __call__(self, *a):
            if len(runs) == 1:
                self.cancel_event.set()
    with pytest.raises(Exception, match="Stopped"):
        behaviour.batch(net, "game", vis=600, log=Log())
    assert behaviour.summary(root)["checked"] == 1
    behaviour.batch(net, "game", vis=600, log=lambda *a: None)
    assert behaviour.summary(root)["checked"] == 3 and runs[1:] == [[0x80000500], [0x80000600]], "it goes on"


def test_readiness_shows_the_last_check(game):
    from decompnet.port.readiness import describe, readiness
    net, root, runs = game
    behaviour.batch(net, "game", vis=600, log=lambda *a: None)
    with net.progress.view():
        report = readiness(net, "game")
    assert report["behaviour_available"] and report["behaviour"]["differs"] == 1
    assert any("1 matched functions behave differently from the original" in b for b in report["blockers"])
    assert "differs: $v0: recompiled 00000007, decompiled 00000000" in describe(report)


def test_a_return_address_c_cannot_store_is_a_note_not_a_difference(tmp_path):
    """A game's heap records who called it (its caller's $ra): the recompiled C stores it, decompiled C has no $ra
    and a port stores 0. That alone isn't a difference; anything else still is."""
    sp = 0x80100000
    jal_word = (3 << 26) | (0x80001000 >> 2 & 0x03FFFFFF)
    before = _mem({0x80020000: jal_word})  # a jal at 80020000: a call made there returns to 80020008
    (tmp_path / "r.rdram").write_bytes(_mem({0x80050000: 0x80020008, 0x80060000: 5}))
    (tmp_path / "p.rdram").write_bytes(_mem({0x80060000: 5}))
    recomp = {"v0": 0, "v1": 0, "f0": 0, "f1": 0, "rdram": tmp_path / "r.rdram"}
    port = {**recomp, "rdram": tmp_path / "p.rdram"}
    notes = []
    assert behaviour.compare_calls(before, recomp, port, sp, lambda a: "gHeapCallerRa", notes=notes) == []
    assert notes == ["gHeapCallerRa: the recompiled C stored the return address 80020008, which C doesn't have"]
    (tmp_path / "r.rdram").write_bytes(_mem({0x80050000: 0x80020010}))  # not after a call: a difference
    assert behaviour.compare_calls(before, recomp, port, sp, hex, notes=[])
