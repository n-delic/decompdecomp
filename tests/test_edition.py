"""A development copy is a separate app from the installed one (app/edition.py), and the old agent bridge is removed
from the user's agents (app/cleanup.py), touching nothing else in their settings."""

import json

from decompnet.app import cleanup, edition, launcher


def test_a_development_copy_lives_apart_from_the_installed_app(monkeypatch, tmp_path):
    monkeypatch.delenv("DECOMP_HOME", raising=False)
    monkeypatch.setattr(edition.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(edition, "DEV", True)
    assert edition.home_dir() == tmp_path / "Decomp-dev"
    monkeypatch.setattr(edition, "DEV", False)
    assert edition.home_dir() == tmp_path / "Decomp"
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path / "elsewhere"))
    assert edition.home_dir() == tmp_path / "elsewhere"
    assert edition.DEV_PORT != edition.RELEASE_PORT and edition.DEV_HIGHLIGHT != edition.RELEASE_HIGHLIGHT


def test_a_development_copys_icon_is_another_colour(monkeypatch):
    monkeypatch.setattr(edition, "HIGHLIGHT", edition.DEV_HIGHLIGHT)
    svg = edition.icon_svg()
    assert "#3ac8f6" in svg and "#f6d43a" not in svg


def test_the_apps_menu_shows_one_decompdecomp(monkeypatch, tmp_path):
    """The entry is named like the packages' and the old decomp-wtf entry goes: the owner's menu showed two."""
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(edition, "DEV", False)
    apps = tmp_path / ".local" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "decomp-wtf.desktop").write_text("[Desktop Entry]\n")
    launcher.install_launcher()
    assert sorted(p.name for p in apps.iterdir()) == ["decompdecomp.desktop"]
    assert f"StartupWMClass={edition.WINDOW_CLASS}" in (apps / "decompdecomp.desktop").read_text()
    monkeypatch.setattr(edition, "DEV", True)
    assert "no launcher" in launcher.install_launcher()


def test_only_the_old_bridge_counts_as_ours():
    assert cleanup.ours({"command": "/home/me/.local/bin/decomp", "args": ["mcp"], "env": {}})
    assert cleanup.ours({"type": "local", "command": ["/opt/decompdecomp/bin/decomp", "mcp"]})  # opencode's form
    assert cleanup.ours({"command": "/usr/bin/python3", "args": ["-m", "decompnet.app.cli", "mcp"]})
    assert cleanup.ours({"type": "remote", "url": "http://127.0.0.1:6464/mcp", "headers": {}})
    assert not cleanup.ours({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]})
    assert not cleanup.ours({"command": "/home/me/.local/bin/decomp", "args": ["setup"]})
    assert not cleanup.ours({"url": "https://mcp.example.com/mcp"})


def test_removing_the_old_bridge_leaves_everything_else(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup.shutil, "which", lambda exe: None)  # no claude/codex here: their files stay unread
    agy = tmp_path / ".gemini" / "config" / "mcp_config.json"
    agy.parent.mkdir(parents=True)
    agy.write_text(json.dumps({"mcpServers": {
        "decomp": {"command": str(tmp_path / ".local/bin/decomp"), "args": ["mcp"], "disabled": False},
        "github": {"command": "npx", "args": ["server-github"]}}}))
    opencode = tmp_path / ".config" / "opencode" / "opencode.json"
    opencode.parent.mkdir(parents=True)
    opencode.write_text(json.dumps({"$schema": "x", "model": "m", "mcp": {
        "decomp": {"type": "remote", "url": "http://127.0.0.1:6464/mcp"}}}))
    zed = tmp_path / ".config" / "zed" / "settings.json"
    zed.parent.mkdir(parents=True)
    zed.write_text('// comments: not plain JSON\n{"context_servers": {}}')
    said = cleanup.remove_registrations(tmp_path)
    assert len(said) == 2 and all("removed the old decompdecomp bridge" in line for line in said)
    assert json.loads(agy.read_text()) == {"mcpServers": {"github": {"command": "npx", "args": ["server-github"]}}}
    assert json.loads(opencode.read_text()) == {"$schema": "x", "model": "m", "mcp": {}}
    assert zed.read_text().startswith("// comments")  # left as it was
    assert cleanup.remove_registrations(tmp_path) == []  # a second run finds nothing


def test_a_development_copy_leaves_the_users_agents_alone(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(cleanup, "remove_registrations", lambda home: ran.append(home) or [])
    monkeypatch.setattr(edition, "DEV", True)
    cleanup.old_registrations(tmp_path)
    assert ran == [] and not (tmp_path / cleanup.DONE_MARKER).exists()


def test_agents_that_arent_installed_lose_the_old_bridge_from_their_files(monkeypatch, tmp_path):
    """Started from the apps menu, the app's PATH lacked ~/.local/bin: Claude Code and Codex counted as not installed
    and kept their old entries, which then failed in every session ("invalid choice: 'mcp'")."""
    monkeypatch.setattr(cleanup.shutil, "which", lambda exe: None)
    bridge = {"command": str(tmp_path / ".local/bin/decomp"), "args": ["mcp"]}
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"decomp": bridge, "other": {"command": "x"}},
                                                       "projects": {}}))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        'model = "gpt"\n\n[mcp_servers.decomp]\ncommand = "/home/me/.local/bin/decomp"\nargs = ["mcp"]\n\n'
        '[mcp_servers.decomp.env]\nDECOMP_AGENT_NAME = "codex"\n\n[mcp_servers.github]\ncommand = "gh"\n')
    said = cleanup.remove_registrations(tmp_path)
    assert [line.split(":")[0] for line in said] == ["Claude Code", "Codex"]
    assert json.loads((tmp_path / ".claude.json").read_text())["mcpServers"] == {"other": {"command": "x"}}
    codex = (tmp_path / ".codex" / "config.toml").read_text()
    assert "decomp" not in codex and 'model = "gpt"' in codex and "[mcp_servers.github]" in codex


def test_an_app_given_its_own_home_leaves_the_users_agents_alone(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(cleanup, "remove_registrations", lambda home: ran.append(home) or ["Codex: removed"])
    monkeypatch.setattr(edition, "DEV", False)
    monkeypatch.setenv("DECOMP_HOME", str(tmp_path))
    cleanup.old_registrations(tmp_path)
    assert ran == []
