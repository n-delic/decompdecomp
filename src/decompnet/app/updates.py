"""Updates: decompdecomp finds out when there's a newer version of itself and, once the user says yes, installs it
and restarts in place (same port, the window again; helper agents carry on afterwards).

Where updates come from depends on how decompdecomp was installed (install.json, written by the installer):
- "release": the newest release on GitHub (main's install.sh says which one), installed from its tag's archive
- "branch":  the newest commit of a branch on GitHub (for testing a branch before it's released), installed from
             GitHub's archive of that commit
Installing is `uv tool install --force` of the new version, then its `decomp setup` (new pinned tools), then the
running app becomes the new version with exec (the process and its port stay the same, and it opens its window again).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import __version__, toolchains
from . import edition, window

REPO = "n-delic/decompdecomp"
RECHECK = {"release": 3 * 3600, "branch": 15 * 60}  # GitHub allows 60 unauthenticated API calls an hour


def info_file() -> Path:
    """How this copy was installed. Outside uv's tool environment, which every install replaces."""
    return toolchains.data_dir() / "install.json"


def installed() -> dict:
    """How this copy was installed (install.json): from releases or from a branch, and its version."""
    try:
        data = json.loads(info_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("channel") not in ("release", "branch"):
        data = {"channel": "release"}
    if data["channel"] == "branch" and data.get("branch") in ("", None, "main"):
        # main is the release line: installed from a checkout of it, every commit (a website edit too) was offered
        # as an update; it updates on releases, as an install from the installer does
        data = {**data, "channel": "release", "branch": "", "commit": ""}
    data.setdefault("repo", REPO)
    data["version"] = __version__
    return data


def record(channel: str, branch: str = "", commit: str = "", repo: str = REPO) -> dict:
    """Write how this copy was installed, for the next update check."""
    data = {"channel": channel if channel in ("release", "branch") else "release", "repo": repo or REPO,
            "branch": branch, "commit": commit, "version": __version__, "t": time.time()}
    if data["channel"] == "release":
        data.update(branch="", commit="")
    info_file().parent.mkdir(parents=True, exist_ok=True)
    info_file().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def version_key(v: str) -> tuple[int, ...]:
    """(major, minor, patch, stage, n), so a pre-release comes before its release: 1.0.0-alpha (or 1.0.0a0, as
    Python spells it) < 1.0.0-beta.2 < 1.0.0rc1 < 1.0.0. Comparing only the numbers, an alpha never updated to its
    release."""
    m = re.match(r"\D*(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)", v.strip())
    if not m:
        return (0,)
    nums = tuple(int(x or 0) for x in m.group(1, 2, 3))
    pre = re.match(r"[-._]?(alpha|beta|rc|a|b|c)[-._]?(\d*)", m.group(4).strip().lower())
    stage = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2, "c": 2}[pre.group(1)] if pre else 3
    return nums + (stage, int(pre.group(2) or 0) if pre else 0)


def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"decompdecomp/{__version__} (update check)",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str) -> dict:
    return json.loads(_get(url))


def _first_line(message: str) -> str:
    return (message or "").strip().splitlines()[0][:140] if (message or "").strip() else ""


def _check_release(cur: dict) -> dict:
    text = _get(f"https://raw.githubusercontent.com/{cur['repo']}/main/site/public/install.sh").decode("utf-8", "replace")
    m = re.search(r'^VERSION="([^"]+)"', text, re.M)
    if not m:
        raise ValueError("decompdecomp didn't say which version is current")
    latest = m.group(1)
    newer = version_key(latest) > version_key(cur["version"])
    spec = f"decompnet @ https://github.com/{cur['repo']}/archive/refs/tags/v{latest}.tar.gz"
    return {"available": newer, "latest": {"version": latest, "commit": "", "date": None},
            "notes": [], "link": f"https://github.com/{cur['repo']}/commits/main", "spec": spec}


def _branch_version(repo: str, sha: str) -> str:
    try:
        text = _get(f"https://raw.githubusercontent.com/{repo}/{sha}/pyproject.toml").decode("utf-8", "replace")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        return m.group(1) if m else ""
    except (OSError, ValueError):
        return ""


def _check_branch(cur: dict) -> dict:
    repo, branch, have = cur["repo"], cur.get("branch") or "main", cur.get("commit") or ""
    head = _json(f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(branch, safe='')}")
    sha = head["sha"]
    notes: list[str] = []
    available = bool(sha) and sha != have
    if available and have:
        try:  # what changed, and whether the branch is really ahead of what's installed (not behind it)
            cmp = _json(f"https://api.github.com/repos/{repo}/compare/{have}...{sha}")
            if cmp.get("status") in ("identical", "behind"):
                available = False
            notes = [_first_line(c["commit"]["message"]) for c in reversed(cmp.get("commits") or [])][:12]
        except (urllib.error.HTTPError, KeyError, ValueError):  # a commit GitHub doesn't know (never pushed)
            pass
    if available and not notes:
        notes = [_first_line(head["commit"]["message"])]
    return {"available": available,
            "latest": {"version": _branch_version(repo, sha) if available else cur["version"], "commit": sha,
                       "date": (head.get("commit") or {}).get("committer", {}).get("date")},
            "notes": [n for n in notes if n], "link": f"https://github.com/{repo}/commits/{branch}",
            "spec": f"decompnet @ https://github.com/{repo}/archive/{sha}.tar.gz"}


def check() -> dict:
    """What's installed, what's newest, and whether that's an update (network: GitHub or decompdecomp)."""
    cur = installed()
    out = {"current": cur, "available": False, "latest": None, "notes": [], "link": None, "error": None,
           "checked": time.time(), "can_apply": can_apply()[0], "why_not": can_apply()[1]}
    try:
        out.update(_check_branch(cur) if cur["channel"] == "branch" else _check_release(cur))
    except (OSError, ValueError, KeyError) as e:
        out["error"] = f"couldn't check for updates: {e}"
    return out


def uv() -> str | None:
    """Where uv is, which installs updates: on PATH, or where its installer puts it."""
    found = shutil.which("uv")
    if found:
        return found
    for d in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):
        if (d / "uv").exists():
            return str(d / "uv")
    return None


def can_apply() -> tuple[bool, str]:
    if (package := os.environ.get("DECOMP_PACKAGE")):  # set by the .deb's, the .rpm's and the AppImage's launchers
        kind = {"appimage": "an AppImage", "rpm": "an .rpm"}.get(package, "a .deb")
        return False, f"this decompdecomp was installed as {kind}: download the new one from https://github.com/{REPO}/releases"
    if edition.DEV:  # installing a release from a checkout would put a second, installed app beside it
        return False, "this is a development copy (a git checkout): update it with git pull"
    if not uv():
        return False, "uv isn't installed here, so decompdecomp can't update itself: run the installer again"
    return True, ""


def install(found: dict, log=print) -> dict:
    """Install the version check() found. Returns the new install info (not yet the running version)."""
    ok, why = can_apply()
    if not ok:
        raise RuntimeError(why)
    cur = found["current"]
    log(f"installing decompdecomp {found['latest'].get('version') or ''}".rstrip()
        + (f" ({cur.get('branch')} @ {found['latest']['commit'][:7]})" if cur["channel"] == "branch" else ""))
    cmd = [uv(), "tool", "install", "--force", "--python", "3.12", found["spec"]]
    _stream(cmd, log, "installing the update failed")
    bindir = subprocess.run([uv(), "tool", "dir", "--bin"], capture_output=True, text=True).stdout.strip()
    decomp = Path(bindir) / "decomp" if bindir else None
    if not decomp or not decomp.exists():
        raise RuntimeError("the update was installed, but its decomp command isn't there")
    log("getting the tools this version needs")
    _stream([str(decomp), "setup"], log, "setting up the new version's tools failed")
    data = {"channel": cur["channel"], "repo": cur.get("repo") or REPO, "branch": cur.get("branch") or "",
            "commit": found["latest"].get("commit") or "", "version": found["latest"].get("version") or "",
            "t": time.time()}
    info_file().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def _stream(cmd: list[str], log, failure: str) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=dict(os.environ, NO_COLOR="1"))
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-20:]
            log(f"  {line}")
    if proc.wait() != 0:
        raise RuntimeError(f"{failure} (exit code {proc.returncode}): {' / '.join(tail[-3:])}")


def restart(port: int, secret: str, window: bool) -> None:
    """Become the newly installed version: same process, same port and secret, and the window again when it was
    open (the old one closes as this process is replaced). Never returns."""
    env = dict(os.environ, DECOMP_RESTART=json.dumps({"port": port, "secret": secret, "window": window}))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    argv = [sys.executable, "-m", "decompnet.app.cli", "--port", str(port)]
    if not Path(sys.executable).exists():  # the environment moved: its decomp command starts the same way
        bindir = subprocess.run([uv() or "uv", "tool", "dir", "--bin"], capture_output=True, text=True).stdout.strip()
        argv = [str(Path(bindir) / "decomp"), "--port", str(port)]
    os.execve(argv[0], argv, env)


class Updater:
    """The app's update state: the last check (cached) and an update being installed."""

    def __init__(self, before_restart, port: int, secret: str):
        self.before_restart, self.port, self.secret = before_restart, port, secret
        self.last: dict | None = None
        self.job = {"state": "idle", "log": [], "error": None}
        self._lock = threading.Lock()

    def status(self, refresh: bool = False) -> dict:
        cur = self.last
        channel = installed()["channel"]
        if refresh or cur is None or time.time() - cur.get("checked", 0) > RECHECK[channel]:
            self.last = cur = check()
        return {**cur, "job": {k: (v[-200:] if k == "log" else v) for k, v in self.job.items()}}

    def apply(self) -> None:
        """Install the update in the background, then restart into it."""
        with self._lock:
            if self.job["state"] in ("installing", "restarting"):
                return
            # never the cached check: on a branch that moved since, it names an older commit than the newest
            found = self.last = check()
            if not found.get("available"):
                raise ValueError(found.get("error") or "decompdecomp is up to date")
            ok, why = can_apply()
            if not ok:
                raise ValueError(why)
            self.job = {"state": "installing", "log": [], "error": None}

        def log(line: str) -> None:
            self.job["log"].append(str(line))

        def run() -> None:
            try:
                install(found, log=log)
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                self.job.update(state="error", error=str(e))
                return
            self.job["state"] = "restarting"
            log("restarting decompdecomp")
            time.sleep(1)  # the page sees "restarting" before the server goes away
            try:
                self.before_restart()
                restart(self.port, self.secret, window.is_open())
            except OSError as e:  # still the old version: say so (`decomp` starts the new one)
                self.job.update(state="error", error=f"the update is installed, but restarting failed ({e}): "
                                                       "quit decompdecomp and start it again")

        threading.Thread(target=run, daemon=True, name="update").start()
