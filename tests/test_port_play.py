"""Play (port/wasm/play.py): the app builds a game's WebAssembly build from its save and runs it in the browser."""

import subprocess
import sys

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
