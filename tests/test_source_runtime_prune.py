"""Old staged source runtimes are deleted (app/source_runtime.py): they piled up to 11 GB in one home."""

import os

from decompnet.app import source_runtime


def test_prune_keeps_newest_and_running(tmp_path, monkeypatch):
    root = tmp_path / "source-runtimes"
    for i in range(6):
        d = root / f"main-{i}"
        d.mkdir(parents=True)
        os.utime(d, (1000 + i, 1000 + i))
    (root / "other").mkdir()  # not a staged runtime
    monkeypatch.setattr(source_runtime.sys,
                        "prefix", str(root / "main-0" / ".venv"))
    removed = source_runtime.prune(root, keep=[root / "main-2"])
    left = sorted(p.name for p in root.iterdir())
    assert left == ["main-0", "main-2", "main-4", "main-5", "other"]  # running, kept, two newest
    assert sorted(p.name for p in removed) == ["main-1", "main-3"]


def test_discard_removes_unused_staged(tmp_path):
    staged = tmp_path / "main-x"
    (staged / "src").mkdir(parents=True)
    source_runtime.discard(staged)
    source_runtime.discard(None)
    assert not staged.exists()
