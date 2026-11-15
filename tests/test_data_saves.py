"""The data's C in the progress folder (app/data_saves.py): written when a unit matches and by a full save, owned by
progress commits, and judged again on restore. On test_data_units' homemade N64 project."""
import json

import pytest

from decompnet import toolchains
from decompnet.app import data_saves, data_units, repository_progress, saves
from decompnet.network import NetworkError
from tests.test_data_units import build

TABLE = "extern void boot(void);\nvoid (*D_80000500[4])(void) = { boot, boot, 0, 0 };"


def home(tmp_path, name):
    (tmp_path / name).mkdir()
    return build(tmp_path / name)


@pytest.fixture
def project(tmp_path, monkeypatch):
    net = home(tmp_path, "home")
    folder = tmp_path / "progress"
    folder.mkdir()
    (folder / saves.MANIFEST).write_text(json.dumps({"functions": {}, "headers": {}}), encoding="utf-8")
    net.save_folder = folder
    monkeypatch.setattr(saves, "default_folder", lambda net_, slug: folder)
    yield net
    if hasattr(net, "autosave"):
        net.autosave.close()


@pytest.fixture
def compiles():
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")


def test_a_match_is_saved_at_once_and_a_restore_judges_it_again(project, compiles, tmp_path):
    net, folder = project, project.save_folder
    with net.progress.view():
        assert data_units.compile_data(net, "game", "D_80000500", TABLE).exact
    index = json.loads((folder / data_saves.INDEX).read_text(encoding="utf-8"))
    entry = index["data"]["D_80000500"]
    assert entry["address"] == "0x80000500" and entry["size"] == 16
    assert (folder / entry["file"]).read_text(encoding="utf-8").strip() == TABLE
    assert {data_saves.INDEX, entry["file"]} <= repository_progress.owned({"functions": {}, "headers": {}}, folder)

    other = home(tmp_path, "other")  # another computer: the same game, nothing matched yet
    lines = []
    result = data_saves.restore(other, "game", folder, lines.append)
    assert result == {"restored": 1, "failed": 0, "saved": 1}
    with other.progress.view():
        assert data_units.unit(other, "game", "D_80000500")["status"] == "matched"
    # a saved definition that no longer holds the game's bytes isn't taken on its word
    (folder / entry["file"]).write_text(TABLE.replace("boot, boot", "boot, 0"), encoding="utf-8")
    third = home(tmp_path, "third")
    assert data_saves.restore(third, "game", folder, lines.append)["failed"] == 1
    if hasattr(other, "autosave"):
        other.autosave.close()
    if hasattr(third, "autosave"):
        third.autosave.close()


def test_a_full_save_writes_every_unit_and_removes_what_isnt_matched(project):
    net, folder = project, project.save_folder
    with net.progress.view():
        proj = net.project_row("game")
        with data_units._store(net, proj).tx() as db:
            db.execute("INSERT INTO data_units(project_id, address, size, span, status, source) VALUES(?,?,?,?,?,?)",
                       (proj["id"], 0x80000510, 4, 4, "matched", "s32 D_80000510 = 7;"))
        assert data_saves.publish(net, "game")
    stale = folder / "data" / "D_OLD.c"
    stale.write_text("old", encoding="utf-8")
    index = json.loads((folder / data_saves.INDEX).read_text(encoding="utf-8"))
    index["data"]["D_OLD"] = {"address": "0x80000520", "file": "data/D_OLD.c"}
    (folder / data_saves.INDEX).write_text(json.dumps(index), encoding="utf-8")
    with net.progress.view():
        data_saves.publish(net, "game")
    assert not stale.exists()  # the save owned it, and it isn't matched any more
    assert (folder / "data" / "D_80000510.c").read_text(encoding="utf-8").strip() == "s32 D_80000510 = 7;"


def test_rebuildable_repositories_track_the_data_files():
    from decompnet.app import rebuildable_repository as repo
    assert repo.public("progress/data.json") and repo.public("progress/data/D_80000500.c")
    assert not repo.public("progress/data/D_80000500.bin")
    assert "!/progress/data/*.c" in repo.ignore()


def test_no_save_folder_yet_means_nothing_written_and_a_broken_index_is_told(project, tmp_path):
    net, folder = project, project.save_folder
    with net.progress.view():
        assert not data_saves.publish(net, "game", tmp_path / "nowhere")
    (folder / data_saves.INDEX).write_text("{not json", encoding="utf-8")
    with pytest.raises(NetworkError, match="Fix data.json"):
        data_saves.owned(folder)
    with pytest.raises(NetworkError, match="can't be read"):
        data_saves.restore(net, "game", folder)
