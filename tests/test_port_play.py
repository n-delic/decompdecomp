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
    them library code, so Play leaves what the host replaces to the host, by the save's own file names."""
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
                             ("func_80032000", "open")):
            ids[name] = db.execute("INSERT INTO functions(project_id,name,size,n_insns,status) VALUES(?,?,?,?,?)",
                                   (pid, name, 64, 16, status)).lastrowid
    # osRecvMesg the host replaces, guMtxIdent the game's own C runs, and one unmatched (no file in the save)
    proven = {ids["func_80030000"]: "osRecvMesg", ids["func_80031000"]: "guMtxIdent", ids["func_80032000"]: "osSendMesg"}
    monkeypatch.setattr(net.twins, "prints", lambda p: {
        fid: SimpleNamespace(key=by_name[n][0], size=by_name[n][1], asm="") for fid, n in proven.items()})
    assert play.library(net, "game", log=lambda line: None) == {"func_80030000.c"}
