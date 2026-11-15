"""Cursor helpers share one copy of the CLI's self-updates and keep only their latest chats (agents/cursor.py)."""

import os

from decompnet.agents import cursor


def test_helpers_share_updates(tmp_path):
    agents = tmp_path / "agents"
    shared = agents / cursor.SHARED
    a, b = agents / "a" / ".cursor-home", agents / "b" / ".cursor-home"
    old = a / ".local/share/cursor-agent/versions/1"
    old.mkdir(parents=True)
    (old / "cursor-agent").write_text("x")
    for home in (a, b):
        cursor.share_updates(home, shared)
        link = home / ".local/share/cursor-agent"
        assert link.is_symlink() and link.resolve() == (shared / "cursor-agent").resolve()
    assert not old.exists() or old.resolve().is_relative_to(shared.resolve())  # the private copy is gone
    cursor.share_updates(a, shared)  # again: unchanged


def test_shared_versions_pruned(tmp_path):
    shared = tmp_path / cursor.SHARED
    versions = shared / "cursor-agent/versions"
    for i in range(4):
        (versions / f"v{i}").mkdir(parents=True)
        os.utime(versions / f"v{i}", (1000 + i, 1000 + i))
    cursor.share_updates(tmp_path / "h", shared)
    assert sorted(p.name for p in versions.iterdir()) == ["v2", "v3"]


def test_old_chats_pruned(tmp_path):
    workspace = tmp_path / "chats" / "abc"
    for i in range(5):
        (workspace / f"c{i}").mkdir(parents=True)
        os.utime(workspace / f"c{i}", (1000 + i, 1000 + i))
    cursor.prune_chats(tmp_path / "chats")
    cursor.prune_chats(tmp_path / "missing")
    assert sorted(p.name for p in workspace.iterdir()) == ["c2", "c3", "c4"]


def test_helpers_use_the_installed_cli(tmp_path):
    root = tmp_path / "share" / "cursor-agent"
    program = root / "versions" / "2026.1" / "cursor-agent"
    program.parent.mkdir(parents=True)
    program.write_text("x")
    (root / "versions" / "2025.1").mkdir()  # an older version: the install's own to clean up
    exe = tmp_path / "bin" / "cursor-agent"
    exe.parent.mkdir()
    exe.symlink_to(program)
    assert cursor.installed_root(exe) == root.resolve()
    assert cursor.installed_root(tmp_path / "bin") is None
    home = tmp_path / "helper" / ".cursor-home"
    cursor.share_updates(home, cursor.installed_root(exe))
    assert (home / ".local/share/cursor-agent").resolve() == root.resolve()
    assert (root / "versions" / "2025.1").is_dir()
