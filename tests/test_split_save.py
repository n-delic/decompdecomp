"""A format 2 save keeps its agents' notes and verification records in files of their own.

On a 2,000-function game they were 25 MB of a 27 MB decompdecomp.json, rewritten whole for every new best score. The
app still works with one joined manifest (saves.manifest_text); only the folder and git hold it split."""
import json
import os
import subprocess

import pytest

from decompnet.app import saves
from decompnet.network import NetworkError
from tests import test_progress_speed as speed

project = speed.project


def _notes(net, pid):
    return [row["text"] for row in net.project_store(pid).all("SELECT text FROM notes WHERE project_id=? ORDER BY id", pid)]


def _add_note(net, pid, text, function="f1"):
    with net.store.tx() as db:
        fid = db.execute("SELECT id FROM functions WHERE project_id=? AND name=?", (pid, function)).fetchone()["id"]
        db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,NULL,?,1)",
                   (pid, fid, text))


def test_split_and_join_give_back_the_same_text():
    manifest = {"format": 1, "saved_at": 1.5, "revision": 3, "title": "Game", "notes": [{"function": "f", "text": "é"}],
                "functions": {"f": {"vram": 4}}, "verification": {"matches": {"f": {"vram": 4}}}, "layout": []}
    text = saves.manifest_text(manifest)
    assert json.loads(text)["format"] == saves.FORMAT and list(json.loads(text))[-2:] == ["notes", "verification"]
    files = saves.split_manifest(text)
    assert set(files) == set(saves.TOP_FILES)
    assert "notes" not in json.loads(files[saves.MANIFEST]) and "verification" not in json.loads(files[saves.MANIFEST])
    assert saves.join_manifest(files[saves.MANIFEST], files) == text
    # Absent side keys are written empty, so a join always gives the same keys back
    bare = saves.manifest_text({"format": 2, "title": "x"})
    assert json.loads(bare)["notes"] == [] and json.loads(bare)["verification"] == {}
    assert saves.join_manifest(*(lambda f: (f[saves.MANIFEST], f))(saves.split_manifest(bare))) == bare


def test_an_older_manifest_is_its_own_text():
    old = json.dumps({"format": 1, "notes": [{"text": "kept"}], "title": "x"}, indent=1) + "\n"
    assert saves.join_manifest(old, {}) == old
    assert saves.split_manifest(old) == {saves.MANIFEST: old}


def test_a_sync_writes_the_split_save(project):
    net, folder, pid, _, _ = project
    _add_note(net, pid, "a note")
    assert net.progress.sync("game", force=True) is True
    manifest = json.loads((folder / saves.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["format"] == 2 and "notes" not in manifest and "verification" not in manifest
    assert [note["text"] for note in json.loads((folder / saves.NOTES_FILE).read_text(encoding="utf-8"))] == ["a note"]
    assert json.loads((folder / saves.VERIFICATION_FILE).read_text(encoding="utf-8")) == {}
    assert saves.read_manifest(folder)["notes"][0]["text"] == "a note"
    assert net.progress.sync("game", force=True) is False  # settled: reading it back is what was written


def test_a_new_best_score_leaves_the_notes_file_alone(project):
    net, folder, pid, _, _ = project
    _add_note(net, pid, "a note")
    net.progress.sync("game", force=True)
    notes = (folder / saves.NOTES_FILE).stat()
    manifest = (folder / saves.MANIFEST).stat()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=3 WHERE project_id=? AND name=?", (pid, f"f{speed.FUNCTIONS - 1}"))
    assert net.progress.sync("game", force=True) is True
    assert (folder / saves.NOTES_FILE).stat().st_mtime_ns == notes.st_mtime_ns
    assert (folder / saves.MANIFEST).stat().st_mtime_ns != manifest.st_mtime_ns


def test_an_edit_to_the_notes_file_is_read(project):
    net, folder, pid, _, _ = project
    _add_note(net, pid, "first")
    net.progress.sync("game", force=True)
    notes = json.loads((folder / saves.NOTES_FILE).read_text(encoding="utf-8"))
    notes.append({"function": "f2", "text": "from a pull", "by": ""})
    (folder / saves.NOTES_FILE).write_text(json.dumps(notes, indent=1) + "\n", encoding="utf-8")
    net.progress.sync("game", force=True)
    assert "from a pull" in _notes(net, pid)


def test_an_older_save_is_read_and_converted_by_its_next_write(project):
    net, folder, pid, _, _ = project
    _add_note(net, pid, "kept")
    net.progress.sync("game", force=True)
    # The folder as a format 1 save left it: the notes inside its manifest, no side files
    joined = json.loads(saves.read_manifest_text(folder))
    joined["format"] = 1
    (folder / saves.MANIFEST).write_text(json.dumps(joined, indent=1) + "\n", encoding="utf-8")
    for name in saves.SIDE_FILES.values():
        (folder / name).unlink()
    net.progress.sync("game", force=True)
    assert _notes(net, pid) == ["kept"]
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=4 WHERE project_id=? AND name=?", (pid, f"f{speed.FUNCTIONS - 1}"))
    net.progress.sync("game", force=True)
    assert json.loads((folder / saves.MANIFEST).read_text(encoding="utf-8"))["format"] == 2
    assert [note["text"] for note in json.loads((folder / saves.NOTES_FILE).read_text(encoding="utf-8"))] == ["kept"]


def test_a_missing_notes_file_stops_the_sync_and_keeps_the_notes(project):
    """Read as "no notes", a partial copy (or an old .gitignore) would have the sync delete the project's notes."""
    net, folder, pid, _, _ = project
    _add_note(net, pid, "precious")
    net.progress.sync("game", force=True)
    os.unlink(folder / saves.NOTES_FILE)
    with pytest.raises(NetworkError, match="notes.json is missing"):
        saves.read_manifest(folder)
    try:
        net.progress.sync("game", force=True)
    except NetworkError as error:
        assert "notes.json" in str(error)
    assert _notes(net, pid) == ["precious"]


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def test_a_commit_holds_the_split_files_and_reads_back_joined(tmp_path):
    from decompnet.app import repository_progress
    if subprocess.run(["git", "--version"], capture_output=True).returncode:
        pytest.skip("git is not installed")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    manifest = {"format": 2, "saved_at": 1.0, "revision": 1, "title": "Game", "functions": {},
                "notes": [{"function": "f", "text": "in git"}], "verification": {"matches": {}}}
    blobs = repository_progress.manifest_blobs("progress/", manifest)
    assert set(blobs) == {"progress/" + name for name in saves.TOP_FILES}
    for path, data in blobs.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "save")
    text = repository_progress.committed_manifest_text(root, "progress/" + saves.MANIFEST)
    assert text == saves.manifest_text(manifest)
    assert json.loads(text)["notes"][0]["text"] == "in git"
    assert repository_progress.owned({**manifest, "headers": {}}) >= {saves.NOTES_FILE, saves.VERIFICATION_FILE}
    assert saves.NOTES_FILE not in repository_progress.owned({"format": 1, "functions": {}, "headers": {}})
