"""Community decomps listed by RetroReversing's directory, offered on the "Continue a public decomp" page so a user
can pick one instead of pasting its address. Only entries whose project type is "Decompilation" on a console
decompdecomp supports are shown: the directory also lists ports, recompilations and tools, which can't be forked.
"""

from __future__ import annotations

import httpx

from ..network import NetworkError

DIRECTORY = "https://www.retroreversing.com/decompilations.json"
# The directory's platform names, and the console id decompdecomp gives a project forked from it.
PLATFORMS = {
    "nintendo 64": "n64", "playstation 1": "ps", "playstation": "ps", "playstation 2": "ps2",
    "playstation 3": "ps3", "playstation portable": "psp", "nintendo gamecube": "gc", "nintendo wii": "wii",
    "pc (windows)": "pc", "pc (dos / windows)": "pc", "nintendo ds": "nds", "nintendo 3ds": "3ds",
    "nintendo switch": "switch", "game boy advance": "gba", "xbox": "xbox", "xbox 360": "xbox360",
}


def _projects(data) -> list[tuple[str, dict]]:
    """The entries of the combined file: {console: {"projects": {key: entry}}}, {"projects": {key: entry}},
    {key: entry} or a list of entries."""
    if isinstance(data, dict) and "projects" not in data and all(
            isinstance(v, dict) and isinstance(v.get("projects"), dict) for v in data.values()):
        return [e for v in data.values() for e in _projects(v)]
    if isinstance(data, dict):
        data = data.get("projects", data)
    if isinstance(data, dict):
        return [(str(k), v) for k, v in data.items() if isinstance(v, dict)]
    if isinstance(data, list):
        return [(str(v.get("id") or v.get("name") or ""), v) for v in data if isinstance(v, dict)]
    return []


def parse(data) -> list[dict]:
    """The decompilations on supported consoles, most recently active first."""
    from .. import consoles
    out = []
    for key, p in _projects(data):
        if str(p.get("project_type") or "").lower() != "decompilation":
            continue
        url = str(p.get("repository_url") or "").strip()
        names = p.get("platforms") or ([p["platform"]] if p.get("platform") else [])
        platform = next((PLATFORMS[n.lower()] for n in names if isinstance(n, str) and n.lower() in PLATFORMS), "")
        if not url.startswith("https://") or not platform:
            continue
        act = p.get("activity") or {}
        out.append({"id": key, "name": str(p.get("name") or key), "repo_url": url, "platform": platform,
                    "platform_name": consoles.name(platform),
                    "status": act.get("status") or p.get("legacy_status") or "",
                    "last_commit_at": act.get("last_commit_at") or "", "stars": act.get("github_stars"),
                    "archived": bool(act.get("archived")), "completion": p.get("completion_status"),
                    "ai_used": p.get("ai_used")})
    out.sort(key=lambda p: p["last_commit_at"], reverse=True)
    return out


def listing() -> list[dict]:
    try:
        r = httpx.get(DIRECTORY, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return parse(r.json())
    except (httpx.HTTPError, ValueError) as e:
        raise NetworkError(f"Couldn't read the community decomp directory ({e}). Check your connection, "
                           "or paste a repository's address instead.") from e
