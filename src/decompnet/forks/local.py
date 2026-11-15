"""Find local checkouts without fetching, building, or changing their files.

A collection often has platform folders above its repositories. Stop at each checkout so its submodules and
private analysis folders do not become extra projects. Symlinked collections are not followed recursively.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..hostenv import NO_WINDOW
from . import policy
from .project import ForkProject

MAX_DEPTH = 6
MAX_DIRECTORIES = 10000
MAX_REPOSITORIES = 5000
SKIP = {"node_modules", "vendor", "build", "dist", "target", "toolchains", "__pycache__"}


def checkout(path: Path) -> Path:
    """Validate an exact working-tree root, including Git worktrees whose .git is a file."""
    path = path.expanduser().resolve()
    if not path.is_dir() or not (path / ".git").exists():
        raise ValueError("Choose a checked-out Git repository.")
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=10,
                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if result.returncode or Path(result.stdout.strip()).resolve() != path:
        raise ValueError("This folder is not the root of a checked-out Git repository.")
    return path


def local_policy(root: Path) -> dict:
    """Read the same contribution files as remote imports, using the current local contents."""
    files, lines = [], []
    for folder in (root, root / "docs", root / ".github"):
        if not folder.is_dir() or folder.is_symlink():
            continue
        for file in sorted(folder.iterdir()):
            relative = file.relative_to(root).as_posix()
            if file.is_symlink() or not file.is_file() or not policy._POLICY_FILE.match(relative):
                continue
            if len(files) >= 10:
                break
            files.append(relative)
            with file.open(encoding="utf-8", errors="replace") as stream:
                text = stream.read(400000)
            lines.extend({"file": relative, "text": line} for line in policy.lines_about_ai(text))
    lines = lines[:12]
    return {"files": files, "lines": lines, "lean": policy.lean([line["text"] for line in lines]), "error": None}


def describe(root: Path) -> dict:
    root = checkout(root)
    project = ForkProject(root, create_state=False)
    return {"path": str(root), "name": root.name, "kind": project.kind(), "versions": project.versions(),
            "policy": local_policy(root)}


def scan(folder: Path) -> dict:
    """A bounded recursive scan. Report unreadable or invalid entries and any truncated traversal."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("Choose an existing folder containing your checked-out projects.")
    pending, seen, projects, errors = [(folder, 0)], set(), [], []
    limited = False
    while pending:
        if len(seen) >= MAX_DIRECTORIES or len(projects) >= MAX_REPOSITORIES:
            limited = True
            break
        path, depth = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            if (path / ".git").exists():
                projects.append(describe(path))
                continue
            with os.scandir(path) as entries:
                children = sorted((Path(entry.path) for entry in entries if not entry.name.startswith(".")
                                   and entry.name not in SKIP and entry.is_dir(follow_symlinks=False)), reverse=True)
            if depth >= MAX_DEPTH:
                limited = limited or bool(children)
            else:
                pending.extend((child, depth + 1) for child in children)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            errors.append({"path": str(path), "error": str(error)})
    return {"folder": str(folder), "projects": projects, "errors": errors, "limited": limited}
