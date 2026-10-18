"""The old agent bridge, removed from the agents it was registered with.

Until the rewrite, `decomp connect` (and the README, for other clients) registered `decomp mcp` with the user's own
agents, and every open session of them started decompdecomp again when it wasn't running: the owner force-quit the
app and it came back (two Claude Code sessions each had a bridge running). The bridge is gone; its registrations go
too, the first time the new version starts, and the app says which.

An entry counts as ours only when it runs `decomp mcp` (any install's decomp, or python -m decompnet.app.cli mcp) or
points at the app's own address (http://127.0.0.1:64xx/mcp, the token clients got from the old settings page).
Nothing else in those files changes.

Claude Code and Codex are asked through their own `mcp remove` (they rewrite their files themselves: editing
~/.claude.json while a session writes it would lose one of the two). Antigravity's file is edited directly: running
`agy` starts the MCP servers in its settings, the old bridge included, and that would start the app.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import tomllib
from pathlib import Path

from . import edition

CLI_TIMEOUT_SECONDS = 30
AGENT_DIRS = (".local/bin", ".claude/local", ".npm-global/bin", ".opencode/bin")  # under the home folder
DONE_MARKER = "registrations-removed"  # in the app's own folder: the cleanup runs once
OWN_URL = re.compile(r"^https?://(127\.0\.0\.1|localhost):\d+/mcp/?$")

# MCP clients that keep JSON settings: (name, file under the home folder, the key that holds the servers)
JSON_CLIENTS = (
    ("Antigravity CLI", ".gemini/config/mcp_config.json", "mcpServers"),
    ("Gemini CLI", ".gemini/settings.json", "mcpServers"),
    ("opencode", ".config/opencode/opencode.json", "mcp"),
    ("Cursor", ".cursor/mcp.json", "mcpServers"),
    ("Claude Desktop", ".config/Claude/claude_desktop_config.json", "mcpServers"),
    ("Windsurf", ".codeium/windsurf/mcp_config.json", "mcpServers"),
    ("VS Code", ".config/Code/User/mcp.json", "servers"),
    ("Cline", ".config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json", "mcpServers"),
    ("Zed", ".config/zed/settings.json", "context_servers"),
)


def ours(entry) -> bool:
    """Whether an MCP server entry is the old decompdecomp bridge."""
    if not isinstance(entry, dict):
        return False
    if OWN_URL.match(str(entry.get("url") or entry.get("serverUrl") or "")):
        return True
    command = entry.get("command")
    argv = ([str(a) for a in command] if isinstance(command, list) else [str(command or "")]) + \
        [str(a) for a in entry.get("args") or []]
    if not argv[0]:
        return False
    runs_decomp = Path(argv[0]).name == "decomp" or "decompnet.app.cli" in argv
    return runs_decomp and "mcp" in argv[1:]


def _json_client(name: str, path: Path, key: str) -> list[str]:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # not there, or not plain JSON (comments): left as it is
        return []
    servers = settings.get(key) if isinstance(settings, dict) else None
    if not isinstance(servers, dict):
        return []
    gone = [server for server, entry in servers.items() if ours(entry)]
    if not gone:
        return []
    for server in gone:
        del servers[server]
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return [f"{name}: removed the old decompdecomp bridge ({', '.join(gone)}) from {path}"]


def _find(exe: str, home: Path) -> str | None:
    """An agent's command, on PATH or where its installer puts it: started from the apps menu, the app's PATH often
    lacks ~/.local/bin, and a "not installed" Claude Code kept its old entry."""
    return shutil.which(exe) or next((str(d / exe) for d in (home / rel for rel in AGENT_DIRS) if (d / exe).exists()), None)


def _cli_client(name: str, exe: str, servers: list[str], extra: list[str], home: Path, by_hand) -> list[str]:
    """Remove `servers` with the agent's own `mcp remove`; without the agent installed, from its file by hand
    (`by_hand(server)`: nothing else writes that file then)."""
    found = _find(exe, home)
    said = []
    for server in servers:
        if found is None:
            removed = by_hand(server)
        else:
            try:
                removed = subprocess.run([found, "mcp", "remove", *extra, server], capture_output=True, text=True,
                                         timeout=CLI_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                removed = False
        if removed:
            said.append(f"{name}: removed the old decompdecomp bridge ({server})")
    return said


def _claude_by_hand(home: Path, server: str) -> bool:
    path = home / ".claude.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        del settings["mcpServers"][server]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True


def _codex_by_hand(home: Path, server: str) -> bool:
    """[mcp_servers.<server>] and its sub-tables ([mcp_servers.<server>.env]) out of Codex's TOML, the rest as it is
    (Python reads TOML but can't write it: the lines go, nothing else changes)."""
    path = home / ".codex" / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False
    table = re.compile(rf"^\s*\[\s*mcp_servers\.{re.escape(server)}(\.[^\]]*)?\s*\]\s*$")
    out, skipping, removed = [], False, False
    for line in lines:
        if line.lstrip().startswith("["):
            skipping = bool(table.match(line))
            removed |= skipping
        if not skipping:
            out.append(line)
    if removed:
        path.write_text("".join(out), encoding="utf-8")
    return removed


def _claude_servers(home: Path) -> list[str]:
    try:
        settings = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s, e in (settings.get("mcpServers") or {}).items() if ours(e)]


def _codex_servers(home: Path) -> list[str]:
    try:
        settings = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s, e in (settings.get("mcp_servers") or {}).items() if ours(e)]


def remove_registrations(home: Path) -> list[str]:
    """Remove the old bridge from every agent of this user (`home`: their home folder). Returns what was removed."""
    said = _cli_client("Claude Code", "claude", _claude_servers(home), ["-s", "user"], home,
                       lambda server: _claude_by_hand(home, server))
    said += _cli_client("Codex", "codex", _codex_servers(home), [], home, lambda server: _codex_by_hand(home, server))
    for name, rel, key in JSON_CLIENTS:
        said += _json_client(name, home / rel, key)
    return said


def old_registrations(internal: Path) -> None:
    """Once, in the installed app on the user's own projects (a development copy, or an app given a home of its own
    with DECOMP_HOME, a test or an experiment, leaves the user's agents alone), in the background so the window
    doesn't wait for the agents' commands. What it removed is kept beside the marker for the app to show (report)."""
    marker = internal / DONE_MARKER
    if edition.DEV or os.environ.get("DECOMP_HOME") or marker.exists():
        return

    def run():
        log = logging.getLogger("decompdecomp")
        try:
            said = remove_registrations(Path.home())
            for line in said:
                log.info(line)
                print(line, flush=True)
            marker.write_text(json.dumps(said) + "\n", encoding="utf-8")
        except Exception:  # never stands between the user and the app
            log.exception("removing the old agent bridge failed")
    threading.Thread(target=run, daemon=True, name="cleanup").start()


def report(internal: Path) -> list[str]:
    """What the cleanup removed, for the app to show once (an older marker said only "done")."""
    try:
        said = json.loads((internal / DONE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return said if isinstance(said, list) else []
