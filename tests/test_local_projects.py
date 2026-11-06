"""Local collections: discover and register checkouts without changing their work or running project code."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.forks import local

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def repo(folder):
    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    (folder / "README.md").write_text("AI contributions are welcome, with human review.")
    (folder / "objdiff.json").write_text(json.dumps({"units": []}))
    return folder.resolve()


def test_scan_finds_platform_collections_and_leaves_checkouts_untouched(tmp_path):
    collection = tmp_path / "collection"
    root = repo(collection / "platform" / "game")
    repo(root / "tools" / "nested")
    repo(collection / ".private" / "hidden")
    repo(collection / "build" / "hidden")
    before = set(root.rglob("*"))
    result = local.scan(collection)
    assert [p["path"] for p in result["projects"]] == [str(root)]
    project = result["projects"][0]
    assert project["kind"] == "objdiff" and project["policy"]["lean"] == "yes"
    assert set(root.rglob("*")) == before
    assert not (root / ".decomp-wtf").exists()
    assert not result["errors"] and not result["limited"]


def test_scan_reports_bad_repos_and_limits_without_losing_valid_ones(tmp_path, monkeypatch):
    good = repo(tmp_path / "a")
    (tmp_path / "bad" / ".git").mkdir(parents=True)
    result = local.scan(tmp_path)
    assert [p["path"] for p in result["projects"]] == [str(good)]
    assert len(result["errors"]) == 1
    monkeypatch.setattr(local, "MAX_REPOSITORIES", 1)
    assert local.scan(tmp_path)["limited"]
    with pytest.raises(ValueError, match="existing folder"):
        local.scan(tmp_path / "missing")


def test_import_keeps_dirty_work_and_branch_and_is_idempotent(tmp_path):
    root = repo(tmp_path / "collection" / "game")
    (root / "private.rom").write_bytes(b"synthetic private file")
    before = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--branch"],
                            capture_output=True, text=True, check=True).stdout
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    result = net.forks.import_local(root.parent, [str(root), str(root)], copies=False)
    assert not result["errors"] and len(result["imported"]) == 1
    slug = result["imported"][0]["slug"]
    project = net.project_row(slug)
    assert Path(project["root"]).resolve() == root
    assert not json.loads(project["config"])["ready"]
    assert net.forks.scan_local(root.parent)["projects"][0]["existing"] == slug
    assert net.forks.import_local(root.parent, [str(root)])["imported"][0]["existing"]
    after = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--branch"],
                           capture_output=True, text=True, check=True).stdout
    assert after == before
    assert (root / "private.rom").read_bytes() == b"synthetic private file"
    assert not (root / ".decomp-wtf").exists()


def test_import_continues_after_failure_and_rejects_outside_selection(tmp_path):
    good = repo(tmp_path / "collection" / "good")
    outside = repo(tmp_path / "outside")
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    result = net.forks.import_local(good.parent, [str(outside), str(good.parent / "missing"), str(good)], copies=False)
    assert len(result["imported"]) == 1 and len(result["errors"]) == 2
    assert "outside" in result["errors"][0]["error"]


def test_analysis_tree_inside_checkout_is_already_imported(tmp_path):
    root = repo(tmp_path / "collection" / "game")
    analysis = root / ".local" / "analysis"
    analysis.mkdir(parents=True)
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    net.forks._register("original", "Original", "", analysis, {"kind": "rom"}, "test")
    assert net.forks.scan_local(root.parent)["projects"][0]["existing"] == "original"
    result = net.forks.import_local(root.parent, [str(root)])
    assert result["imported"][0]["slug"] == "original"
    assert len(net.store.all("SELECT * FROM projects")) == 1


def test_git_worktrees_are_found_and_symlinks_are_not_followed(tmp_path):
    root = repo(tmp_path / "source")
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Synthetic fixture"], check=True)
    worktree = tmp_path / "collection" / "worktree"
    worktree.parent.mkdir()
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(worktree)], check=True)
    assert (worktree / ".git").is_file()
    assert local.scan(worktree.parent)["projects"][0]["path"] == str(worktree.resolve())
    if __import__("os").name != "nt":
        (worktree.parent / "loop").symlink_to(worktree.parent, target_is_directory=True)
        assert len(local.scan(worktree.parent)["projects"]) == 1


def test_default_import_clones_committed_files_and_preserves_original_work(tmp_path):
    root = repo(tmp_path / "collection" / "game")
    (root / "splat.yaml").write_text("options:\n  platform: n64\nsegments: []\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Synthetic fixture"], check=True)
    (root / "README.md").write_text("Uncommitted work stays here")
    (root / "private.rom").write_bytes(b"synthetic private data")
    before = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--branch"],
                            capture_output=True, text=True, check=True).stdout
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    result = net.forks.import_local(root.parent, [str(root)])
    assert not result["errors"]
    project = net.project_row(result["imported"][0]["slug"])
    assert json.loads(project["config"])["platform"] == "n64"
    copied = Path(project["root"])
    assert copied.resolve() != root and copied.is_dir()
    assert "welcome" in (copied / "README.md").read_text()
    assert not (copied / "private.rom").exists()
    assert not (root / ".decomp-wtf").exists()
    after = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--branch"],
                           capture_output=True, text=True, check=True).stdout
    assert after == before
    assert net.forks.import_local(root.parent, [str(root)])["imported"][0]["slug"] == result["imported"][0]["slug"]
    assert net.forks.scan_local(root.parent)["projects"][0]["existing"] == result["imported"][0]["slug"]


@pytest.mark.parametrize("platform", ["n64", "psx", "ps2"])
def test_local_splat_import_reads_platform_and_repairs_old_imports(tmp_path, platform):
    root = repo(tmp_path / "collection" / "game")
    (root / "decompressed.pal.yaml").write_text(
        f"options:\n  platform: {platform}\n  target_path: decompressed.pal.z64\nsegments: []\n")
    (root / "Makefile").write_text("all:\n\t@true\n")
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    result = net.forks.import_local(root.parent, [str(root)], copies=False)
    slug = result["imported"][0]["slug"]
    expected = "ps" if platform == "psx" else platform
    assert json.loads(net.project_row(slug)["config"])["platform"] == expected
    net._update_cfg(slug, lambda cfg: cfg.__setitem__("platform", ""))
    assert net.forks.get(slug)[2]["platform"] == expected
    assert net.summary(slug)["platform"] == expected
    net._update_cfg(slug, lambda cfg: cfg.__setitem__("platform", "n64"))
    assert net.forks.get(slug)[2]["platform"] == "n64"  # keep an explicit selection


def test_platform_detection_tolerates_invalid_splat_and_prefers_manifest(tmp_path):
    from decompnet.forks.project import ForkProject

    root = repo(tmp_path / "game")
    config = root / "splat.yaml"
    config.write_text("options: [\nsegments: []\n")
    assert ForkProject(root, create_state=False).detect_platform() == ""
    config.write_text("options:\n  platform: made-up\nsegments: []\n")
    assert ForkProject(root, create_state=False).detect_platform() == ""
    config.write_text("options:\n  platform: n64\nsegments: []\n")
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "ps2"}))
    assert ForkProject(root, create_state=False).detect_platform() == "ps2"
