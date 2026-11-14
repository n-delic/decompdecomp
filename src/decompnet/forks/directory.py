"""Community decomps listed by RetroReversing's directory, offered on the "Continue a public decomp" page so a user
can pick one instead of pasting its address. Only entries whose project type is "Decompilation" are shown: the
directory also lists ports, recompilations and tools, which can't be forked as decomps.
"""

from __future__ import annotations

import httpx

from ..network import NetworkError

DIRECTORY = "https://raw.githubusercontent.com/RetroReversing/retroReversing/master/_data/decompilations/{}.json"
# The directory's file per console, and the console id decompdecomp gives a project forked from it.
CONSOLES = {"nintendo-64": "n64"}


def parse(data: dict, platform: str = "") -> list[dict]:
    """The decompilations in one directory file, most recently active first."""
    out = []
    for key, p in (data.get("projects") or {}).items():
        if not isinstance(p, dict) or str(p.get("project_type") or "").lower() != "decompilation":
            continue
        url = str(p.get("repository_url") or "").strip()
        if not url.startswith("https://"):
            continue
        act = p.get("activity") or {}
        out.append({"id": key, "name": str(p.get("name") or key), "repo_url": url, "platform": platform,
                    "status": act.get("status") or p.get("legacy_status") or "",
                    "last_commit_at": act.get("last_commit_at") or "", "stars": act.get("github_stars"),
                    "archived": bool(act.get("archived")), "completion": p.get("completion_status"),
                    "ai_used": p.get("ai_used")})
    out.sort(key=lambda p: p["last_commit_at"], reverse=True)
    return out


def listing(console: str = "nintendo-64") -> list[dict]:
    if console not in CONSOLES:
        raise NetworkError(f"The decomp directory has no list for {console!r}.")
    try:
        r = httpx.get(DIRECTORY.format(console), timeout=20, follow_redirects=True)
        r.raise_for_status()
        return parse(r.json(), CONSOLES[console])
    except (httpx.HTTPError, ValueError) as e:
        raise NetworkError(f"Couldn't read the community decomp directory ({e}). Check your connection, "
                           "or paste a repository's address instead.") from e
