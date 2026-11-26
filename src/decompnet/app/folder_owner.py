"""Which decompdecomp home may write a project's own folders (its progress folder, save folder and Git checkouts).

A project's settings name its folders by absolute path. A copy of its database, opened in another home (an experiment
in a scratch `DECOMP_HOME`, a backup restored beside the original), still names the same folders, and every sync of
the copy published the copy's view into them. On an N64 game with many per-function compilers, a scratch run that
reopened eight matched functions in its copy deleted their saved C from the user's real save folder and dropped their
compiler profiles from its manifest; the user's own app then read that folder as edited by the user and reopened all
eight matches ("saved source was removed", "saved source did not verify").

So the settings record which home last wrote those folders. A different home whose settings still name the same
folders, while that home's database still exists, does not write them: the copy has to be pointed at folders of its
own first (which makes it their owner). A home that moved (its old database is gone), a restored save, or folders the
user picked again are adopted, and the record follows them.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..network import NetworkError
from ..store import IN_MEMORY

KEY = "folder_owner"
FOLDER_KEYS = ("save_folder", "auto_commit_folder", "repository_sync_folder")


def home(net) -> str | None:
    """This app's identity: its main database file (one per home). None for a database that only lives in memory."""
    path = getattr(getattr(net, "store", None), "path", None)
    if not path or path == IN_MEMORY:
        return None
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _resolved(value) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return str(value)


def folders(cfg: dict, default: Path) -> list[str]:
    """The folders this project's settings let a sync or a commit write."""
    paths = {_resolved(default)}
    paths.update(_resolved(cfg[key]) for key in FOLDER_KEYS if isinstance(cfg.get(key), str) and cfg.get(key))
    return sorted(paths)


def _owner(cfg: dict) -> dict:
    return cfg.get(KEY) if isinstance(cfg.get(KEY), dict) else {}


def refuse(net, proj, default: Path) -> None:
    """Raise when this home's copy of a project names folders another, still present, home writes.

    Every progress writer finds the folder through saves.default_folder, which calls this, so no write path (a full
    sync, an incremental publish, a draft promotion, a match commit) can reach the original's files."""
    mine = home(net)
    if mine is None:
        return
    # (net.cfg parses a version of the settings once; a big project's are half a megabyte, read on every sync)
    cfg = net.cfg(proj) if hasattr(net, "cfg") else json.loads(proj["config"] or "{}")
    theirs = _owner(cfg).get("home")
    if not theirs or theirs == mine or not Path(theirs).exists():
        return
    shared = sorted(set(folders(cfg, default)) & set(_owner(cfg).get("folders") or ()))
    if shared:
        raise NetworkError(
            f"This project's database is a copy: its save folder {shared[0]} belongs to the decompdecomp home of "
            f"{theirs}, so this copy does not write it. Point the copy's progress, save and commit folders "
            "(progress_folder, save_folder, auto_commit_folder, repository_sync_folder) and its project folder "
            "at folders of its own, then open it again.")


def check(net, proj, default: Path) -> None:
    """refuse, then record this home as the folders' owner (the first sync after an upgrade, a moved home, a copy
    pointed at folders of its own, folders chosen again). Only the sync records it: it runs outside other writes."""
    refuse(net, proj, default)
    mine = home(net)
    if mine is None:
        return
    cfg = net.cfg(proj) if hasattr(net, "cfg") else json.loads(proj["config"] or "{}")
    current = folders(cfg, default)
    if _owner(cfg) == {"home": mine, "folders": current}:
        return
    store = net.project_store(proj["id"])
    with store.tx() as db:
        row = db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()
        if row is None:
            return
        fresh = json.loads(row["config"] or "{}")
        fresh[KEY] = {"home": mine, "folders": current}
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(fresh), proj["id"]))
