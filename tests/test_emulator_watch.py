"""The watch_in_emulator tool (app/emulator_watch.py) on a homemade project: a stand-in for the console's emulator,
and the real one on the user's own ROM when it's installed and DECOMP_TEST_N64_ROM names one (CI has neither)."""
import hashlib
import json
import os
import threading
import time

import pytest

from decompnet import toolchains
from decompnet.app import emulator_watch
from decompnet.app.engine import RomNetwork
from decompnet.app.tools import tool_names
from decompnet.consoles.n64 import emulator
from decompnet.network import NetworkError


def _project(tmp_path, image: bytes, functions, symbols=()):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    (tmp_path / "game.z64").write_bytes(image)
    cfg = dict(kind="rom", platform="n64", rom=dict(file="game.z64", sha1=hashlib.sha1(image).hexdigest()),
               segments=[dict(name="main", rom_start=0x1000, rom_end=0x2000, vram=0x80000400)],
               compiler={"toolchain": "gcc-2.7.2-kmc", "flags": []})
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps(cfg))).lastrowid
        for name, vram, size in functions:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,meta) VALUES(?,?,?,?,?,?,?)",
                       (pid, name, size, size // 4, vram, "matched", "{}"))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, name, vram, size, "func"))
        for name, address, size in symbols:
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, name, address, size, "data"))
    return net


class FakeEmulator:
    """The console part's interface (consoles/n64/emulator.run), answering from a script."""

    def __init__(self, hits, gate=None):
        self.hits, self.gate, self.calls = hits, gate, []

    def run(self, rom, vis, **kw):
        self.calls.append((rom, vis, kw))
        if self.gate is not None:
            self.gate.wait(10)
        return emulator.Run(vis=vis, hits=self.hits)


@pytest.fixture
def game(tmp_path, monkeypatch):
    net = _project(tmp_path, bytes(0x2000), [("start_heading", 0x8001C444, 0xE0), ("place_cars", 0x8001C524, 0x200)],
                   [("gCarMatrix", 0x802A45C8, 0x24)])
    console = type(net.console(net.project_row("game")))
    monkeypatch.setattr(emulator_watch, "_runs", {})
    yield net, console, monkeypatch
    if hasattr(net, "autosave"):
        net.autosave.close()


def test_a_write_watch_names_the_code_that_writes_and_its_caller(game):
    net, console, monkeypatch = game
    fake = FakeEmulator([emulator.Hit("watch", 2454, 0x8001C4A8, 0x8001C5F0, (0x802A45C8, 1, 0, 0), 0)])
    monkeypatch.setattr(console, "emulator", fake)
    with net.progress.view():
        text = emulator_watch.watch(net, "game", "gCarMatrix", "write", vis=2600, from_vi=2400, input="20:A")
    (_, vis, kw), = fake.calls
    assert vis == 2600 and kw["watches"] == [(0x802A45C8, 4)] and kw["report_from"] == 2400 and kw["inputs"] == "20:A"
    assert "gCarMatrix: 1 writes in VIs 2400 to 2600" in text
    assert "written by start_heading+0x64 (pc 8001C4A8), $ra place_cars+0xCC" in text


def test_a_call_watch_gives_the_arguments_and_where_it_came_from(game):
    net, console, monkeypatch = game
    fake = FakeEmulator([emulator.Hit("break", 2452, 0x8001C444, 0x8001C5F8, (0x800B3418, 0x802C88EC, 0, 0), 0,
                                      (0x00000800, 0x10001C1C, 0, 0))])
    monkeypatch.setattr(console, "emulator", fake)
    with net.progress.view():
        text = emulator_watch.watch(net, "game", "start_heading", "call", vis=2500)
    assert fake.calls[0][2]["breaks"] == [0x8001C444]
    assert "called from place_cars+0xCC (returns to 8001C5F8)" in text
    assert "a0-a3 800B3418 802C88EC" in text and "words at a0: 00000800 10001C1C" in text


def test_a_long_run_answers_still_running_then_its_result_once(game):
    net, console, monkeypatch = game
    gate = threading.Event()
    fake = FakeEmulator([emulator.Hit("watch", 7, 0x8001C444, 0, (0, 0, 0, 0), 0)], gate)
    monkeypatch.setattr(console, "emulator", fake)
    monkeypatch.setattr(emulator_watch, "WAIT", 0.2)
    with net.progress.view():
        assert emulator_watch.watch(net, "game", "802A45C8", vis=10).startswith("Still running")
        gate.set()
        time.sleep(0.3)
        text = emulator_watch.watch(net, "game", "0x802A45C8", vis=10)
    assert "written by start_heading (pc 8001C444)" in text
    assert len(fake.calls) == 1, "the same question again collects the run, not a second one"


def test_what_it_refuses_says_what_to_give(game):
    net, console, monkeypatch = game
    monkeypatch.setattr(console, "emulator", FakeEmulator([]))
    with net.progress.view():
        with pytest.raises(NetworkError, match="neither a hex address nor a symbol"):
            emulator_watch.watch(net, "game", "no_such_thing")
        with pytest.raises(NetworkError, match="data, not a function"):
            emulator_watch.watch(net, "game", "gCarMatrix", "call")
        with pytest.raises(NetworkError, match="vis is 1 to"):
            emulator_watch.watch(net, "game", "gCarMatrix", vis=0)
        assert "Nothing hit" in emulator_watch.watch(net, "game", "gCarMatrix", vis=5)
        monkeypatch.setattr(console, "emulator", None)
        with pytest.raises(NetworkError, match="no reversing emulator"):
            emulator_watch.watch(net, "game", "gCarMatrix")


def test_rom_projects_get_the_tool():
    assert "watch_in_emulator" in tool_names(False) and "watch_in_emulator" not in tool_names(True)


ROM = os.environ.get("DECOMP_TEST_N64_ROM", "")


@pytest.mark.skipif(toolchains.installed("mupenre") is None or not os.path.isfile(ROM),
                    reason="needs the mupenre tool and DECOMP_TEST_N64_ROM set to your own N64 ROM")
def test_your_rom_is_watched_at_its_entry_point(tmp_path, monkeypatch):
    image = open(ROM, "rb").read()
    entry = int.from_bytes(image[8:12], "big")
    net = _project(tmp_path, image, [("entrypoint", entry, 0x60)])
    monkeypatch.setattr(emulator_watch, "_runs", {})
    monkeypatch.setattr(emulator_watch, "WAIT", 300)
    with net.progress.view():
        text = emulator_watch.watch(net, "game", "entrypoint", "call", vis=2)
    assert "entrypoint: 1 calls in the first 2 VIs." in text, text
