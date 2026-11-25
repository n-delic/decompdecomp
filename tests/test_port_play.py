"""Play (port/wasm/play.py): the app builds a game's WebAssembly build from its save and runs it in the browser."""

import subprocess
import sys
from contextlib import contextmanager

import pytest

from decompnet.network import NetworkError
from decompnet.port.wasm import play


def test_play_builds_from_the_save_repository_that_holds_the_progress_folder(tmp_path):
    repo = tmp_path / "game"
    (repo / "progress" / "src").mkdir(parents=True)
    (repo / "progress" / "decompdecomp.json").write_text("{}")
    assert play.save_repository(repo / "progress") == repo
    assert play.save_repository(repo) == repo
    with pytest.raises(NetworkError, match="Save the project first"):
        play.save_repository(tmp_path / "unsaved" / "progress")


def test_one_game_runs_per_project_until_it_is_stopped():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    play._running["game"] = (proc, "http://127.0.0.1:1/")
    assert play.running("game") == "http://127.0.0.1:1/"
    assert play.stop("game") and proc.wait(10) is not None
    assert play.running("game") is None and not play.stop("game")


def test_without_a_window_the_game_goes_to_the_browser(monkeypatch):
    """Tests (DECOMP_WINDOW=0) and a browser session open no window: Play says so and the page opens the browser."""
    monkeypatch.setenv("DECOMP_WINDOW", "0")
    assert play._open_window("game", "http://127.0.0.1:1/", 1, "Game (PC port)") is False


def test_play_reads_the_project_without_waiting_for_a_sync(tmp_path):
    """Play waited minutes in the save folder's sync, behind the helpers' upkeep, before its build said anything; the
    build reads the save on disk, so Play reads the project as the page does (progress.view)."""
    class Progress:
        viewing = False

        @contextmanager
        def view(self):
            self.viewing = True
            try:
                yield
            finally:
                self.viewing = False

    class Net:
        progress = Progress()
        read_while_viewing = None

        def _project(self, slug):
            Net.read_while_viewing = self.progress.viewing
            return {"slug": slug, "root": str(tmp_path)}

        def is_rom(self, proj):
            return True

        def cfg(self, proj):
            return {"rom": {"file": "missing.z64"}}

    with pytest.raises(NetworkError, match="ROM isn't at"):
        play.play(Net(), "game", log=lambda line: None)
    assert Net.read_while_viewing is True


def test_play_leaves_out_the_libultra_the_project_proves_and_keeps_what_runs_as_the_games_c(tmp_path, monkeypatch):
    """A save made on another computer, with no SDK indexed and no exclude.txt, named libultra func_<address>: its
    build compiled the Controller Pak's internals and failed (`OSPifRam` undeclared). The built-in signatures prove
    them library code, so Play leaves what the host replaces to the host, by the save's own file names. Unmatched
    library code too: a racing game's save carried an open twin of osEPiStartDma, and the port compiled it over the
    host's, so the game waited for ever on its first DMA."""
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from decompnet import consoles
    from decompnet.app.engine import RomNetwork

    rows = [line.split("\t") for line in Path(consoles.get("n64").sdk_table).read_text().splitlines()
            if not line.startswith("#")]
    unique = {}
    for fingerprint, size, name, _ in rows:
        unique.setdefault(fingerprint, []).append((name, int(size)))
    by_name = {v[0][0]: (k, v[0][1]) for k, v in unique.items() if len(v) == 1}
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps({"kind": "rom", "platform": "n64"}))
                         ).lastrowid
    ids = {}
    with net.project_store(pid).tx() as db:
        for name, status in (("func_80030000", "matched"), ("func_80031000", "matched"), ("race_update", "matched"),
                             ("func_80032000", "open"), ("_Printf", "matched"), ("osEPiStartDma", "open"),
                             ("race_draw", "open")):
            ids[name] = db.execute("INSERT INTO functions(project_id,name,size,n_insns,status) VALUES(?,?,?,?,?)",
                                   (pid, name, 64, 16, status)).lastrowid
    # osRecvMesg the host replaces, guMtxIdent the game's own C runs, osSendMesg unmatched (left out all the same)
    proven = {ids["func_80030000"]: "osRecvMesg", ids["func_80031000"]: "guMtxIdent", ids["func_80032000"]: "osSendMesg"}
    monkeypatch.setattr(net.twins, "prints", lambda p: {
        fid: SimpleNamespace(key=by_name[n][0], size=by_name[n][1], asm="") for fid, n in proven.items()})
    # _Printf: already named as the library's, with no detection to prove it (an N64 game's: IDO's va_arg C)
    # osEPiStartDma: already named, unmatched (a twin from another game); race_draw: the game's own, unmatched
    assert play.library(net, "game", log=lambda line: None) == {"func_80030000.c", "_Printf.c", "func_80032000.c",
                                                                 "osEPiStartDma.c"}


def test_play_leaves_out_the_saves_c_for_functions_the_project_doesnt_count_as_matched(tmp_path):
    """A save copied while it was being rewritten after a recheck still held the C of functions the recheck had
    found wrong (a racing game's switch whose cases followed another game's jump table): its car selection said the
    car broke its class's rules, and it never raced. That C goes; the functions run as the game's own code."""
    import json

    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps({"kind": "rom", "platform": "n64"}))
                         ).lastrowid
    with net.project_store(pid).tx() as db:
        for name, status in (("func_80030000", "matched"), ("func_80031000", "open"), ("func_80032000", "open")):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status) VALUES(?,?,?,?,?)",
                       (pid, name, 64, 16, status))
    (tmp_path / "progress" / "src").mkdir(parents=True)
    (tmp_path / "progress" / "decompdecomp.json").write_text("{}")
    for name in ("func_80030000", "func_80031000"):  # func_80032000: open, with no C in the save
        (tmp_path / "progress" / "src" / f"{name}.c").write_text("void f(void) {}\n")
    said = []
    assert play.unverified(net, "game", log=said.append) == {"func_80031000.c"}
    assert said and "1 functions" in said[0]


def test_play_lists_the_addresses_the_game_ran_that_no_function_starts(tmp_path):
    """The host writes where the game starts running (PORT_ENTRIES) beside the project; Play says which of them no
    function of the project starts, functions the split missed, for the next import to take."""
    import json

    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)",
                         ("game", "game", "mips", 1, str(tmp_path), json.dumps({"kind": "rom", "platform": "n64"}))
                         ).lastrowid
    with net.project_store(pid).tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,vram) VALUES(?,?,?,?,?,?)",
                   (pid, "func_80001000", 0x40, 16, "open", 0x80001000))
    assert play.unlisted_entries(net, "game") == []
    (tmp_path / "runtime-entries.txt").write_text("80001000\n80001020\n80002000\n")
    # 80001020: inside func_80001000 (two functions run together); 80002000: in no function (the split's data)
    assert play.unlisted_entries(net, "game") == [0x80001020, 0x80002000]


def test_parallel_build_keys_never_publish_a_hash_before_its_includes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from decompnet.port.wasm.build import _Files

    source = tmp_path / "unit.c"
    source.write_text('#include "shared.h"\n')
    (tmp_path / "shared.h").write_text("extern int counter;")
    waiting, release = Event(), Event()

    class Includes(dict):
        def __setitem__(self, key, value):
            if key == source and not waiting.is_set():
                waiting.set()
                assert release.wait(5)
            super().__setitem__(key, value)

    files = _Files()
    files.included = Includes()
    with ThreadPoolExecutor() as pool:
        first = pool.submit(files.key, source, [])
        try:
            assert waiting.wait(5)
            second = pool.submit(files.key, source, []).result(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5) == second


def test_relative_analysis_bank_c_is_left_for_runtime_loading(tmp_path):
    import json

    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    segments = [{'name': 'main', 'rom_start': 0x1000, 'rom_end': 0x2000, 'vram': 0x80000400},
                {'name': 'donor_uso_123', 'rom_start': 0x2000, 'rom_end': 0x2100, 'vram': 0}]
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',
                         ('game', 'game', 'mips', 1, str(tmp_path),
                          json.dumps({'kind': 'rom', 'platform': 'n64', 'segments': segments}))).lastrowid
    with net.project_store(pid).tx() as db:
        for name, offset in (('native', 0x1100), ('banked', 0x2020)):
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,status,rom_offset) VALUES(?,?,?,?,?,?)',
                       (pid, name, 16, 4, 'matched', offset))
    assert play.relative_bank_code(net, 'game', log=lambda line: None) == {'banked.c'}


def test_play_gives_the_host_the_console_memory_the_project_names():
    """rom.memory_size (4 or 8, in MB) reaches the host as PORT_MEMSIZE, so a game built for a 4 MB console keeps it."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "src/decompnet/port/wasm/play.py").read_text()
    assert '(cfg.get("rom") or {}).get("memory_size")' in text and 'env["PORT_MEMSIZE"] = str(memory)' in text


def test_playing_beside_the_console_is_for_n64_games_and_its_viewer_ends_with_the_game(tmp_path):
    """Play beside the console (side_by_side.py): one viewer per running game, closed when the game stops, and the
    running view says it is one, so a second click can't swap it for a plain Play."""
    class Net:
        class progress:
            @staticmethod
            @contextmanager
            def view():
                yield

        def _project(self, slug):
            return {"slug": slug}

        def console(self, proj):
            return type("C", (), {"id": "ps1"})()

    with pytest.raises(NetworkError, match="N64 games"):
        play.play(Net(), "game", log=lambda line: None, compare=True)

    closed = []
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    play._running["game"] = (proc, "http://127.0.0.1:1/")
    play._sessions["game"] = type("S", (), {"close": lambda self: closed.append(1)})()
    assert play.running_result("game") == {"url": "http://127.0.0.1:1/", "compare": True}
    assert play.stop("game") and closed == [1] and "game" not in play._sessions


def test_the_viewer_names_addresses_from_the_projects_symbols_and_functions():
    class Store:
        def all(self, sql, *args):
            if "FROM symbols" in sql:
                return [{"name": "gScore", "address": 0x80100000, "size": 4}]
            return [{"name": "func_80001000", "vram": 0x80001000, "size": 0x20}]

    class Net:
        class progress:
            @staticmethod
            @contextmanager
            def view():
                yield

        def _project(self, slug):
            return {"id": 1}

        def project_store(self, project_id):
            return Store()

    names = play.project_symbols(Net(), "game")
    assert names.name(0x80100000) == "gScore" and names.name(0x80001008) == "func_80001000+0x8"
