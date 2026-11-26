"""A copy of a project's database in another home must never write the original's save folder (app/folder_owner.py).

An experiment opened a copy of an N64 project's database in a scratch home; the copy's settings still named the
user's real save folder, so its syncs deleted the saved C of eight functions it had reopened, and the user's own app
then reopened all eight matches as "saved source was removed"."""

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from decompnet.app import folder_owner, saves
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def _home(path: Path, root: Path | None = None) -> RomNetwork:
    path.mkdir(parents=True, exist_ok=True)
    net = RomNetwork(path / "app.db", path)
    if root is not None:
        cfg = {"kind": "rom", "platform": "n64", "rom": {"sha1": "demo"}}
        with net.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES('game','Game',?,?)",
                             (str(root), json.dumps(cfg))).lastrowid
            db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,'',0,'')", (pid,))
            db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,unit,matched_source,best_source,"
                       "best_score,matched_at) VALUES(?,'f',4096,4,1,'matched','','good f','good f',0,?)",
                       (pid, time.time()))
    net.apply_remote_layout = lambda *args: None
    return net


def _copy(net: RomNetwork, home: Path) -> RomNetwork:
    """The experiment recipe: a backup of the database opened in its own home."""
    home.mkdir(parents=True)
    with sqlite3.connect(net.store.path) as source, sqlite3.connect(home / "app.db") as target:
        source.backup(target)
    return _home(home)


def _reopen(net):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',matched_source=NULL,matched_at=NULL,best_source='seed',"
                   "best_score=NULL WHERE name='f'")


def _sync(net):
    try:
        net.progress.sync("game", force=True)
    except NetworkError as error:
        return str(error)
    return net.progress.errors.get("game")


@pytest.fixture
def original(tmp_path):
    root = tmp_path / "project"  # absolute in the settings, so a copy names it too
    root.mkdir()
    net = _home(tmp_path / "home", root)
    save = saves.default_folder(net, "game")
    net.progress.sync("game", force=True)
    assert (save / "src/f.c").read_text() == "good f\n"
    owner = net.cfg(net._project("game"))[folder_owner.KEY]
    assert owner["home"] == str((tmp_path / "home/app.db").resolve()) and str(save.resolve()) in owner["folders"]
    return net, save


def test_a_copy_in_another_home_does_not_write_the_original_save_folder(original, tmp_path):
    net, save = original
    copy = _copy(net, tmp_path / "scratch")
    _reopen(copy)  # what the experiment did in its copy
    manifest = (save / saves.MANIFEST).read_text()
    error = _sync(copy)
    assert error and "copy" in error and str(save.resolve()) in error
    assert (save / "src/f.c").read_text() == "good f\n"  # the saved match is untouched
    assert (save / saves.MANIFEST).read_text() == manifest
    # The original keeps its match and its folder.
    assert net._function("f", "game")["status"] == "matched"
    assert not _sync(net)


def test_a_copy_pointed_at_its_own_folder_becomes_that_folders_owner(original, tmp_path):
    net, save = original
    copy = _copy(net, tmp_path / "scratch")
    shutil.copytree(tmp_path / "project", tmp_path / "scratch/project")
    with copy.store.tx() as db:  # the experiment recipe: the copy's project folder is a copy too
        db.execute("UPDATE projects SET root=?", (str(tmp_path / "scratch/project"),))
    own = saves.default_folder(copy, "game")
    _reopen(copy)
    assert not _sync(copy)
    assert not (own / "src/f.c").exists()  # the copy's own folder follows the copy
    assert copy.cfg(copy._project("game"))[folder_owner.KEY]["home"] == str((tmp_path / "scratch/app.db").resolve())
    assert (save / "src/f.c").read_text() == "good f\n"


def test_a_moved_home_adopts_its_folders(original, tmp_path):
    """The owner's database is gone (the home was moved or restored elsewhere): nothing else writes the folder."""
    net, save = original
    copy = _copy(net, tmp_path / "moved")
    for path in (tmp_path / "home").glob("app.db*"):
        path.unlink()
    _reopen(copy)  # (the owner is recorded by the next sync that writes)
    assert not _sync(copy)
    assert not (save / "src/f.c").exists()
    assert copy.cfg(copy._project("game"))[folder_owner.KEY]["home"] == str((tmp_path / "moved/app.db").resolve())
