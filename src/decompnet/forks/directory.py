"""Community decomps listed by RetroReversing's directory, offered on the "Continue a public decomp" page so a user
can pick one instead of pasting its address. Only entries whose project type is "Decompilation" on a console
decompdecomp supports are shown: the directory also lists ports, recompilations and tools, which can't be forked.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ..network import NetworkError

DIRECTORY = "https://www.retroreversing.com/decompilations.json"
DIRECTORY_PAGE = "https://www.retroreversing.com/source-code/decompiled-retail-console-games"
# Keep this in sync with the image sources allowed by the app's Content Security Policy.
ARTWORK_HOSTS = {
    "www.retroreversing.com", "raw.githubusercontent.com", "upload.wikimedia.org", "thumb.wikimedia.org",
    "opengraph.githubassets.com", "www.thevideogamecompany.com", "vglist.sfo2.digitaloceanspaces.com",
    "images.igdb.com", "s.pacn.ws", "store-images.s-microsoft.com", "i.ytimg.com", "mario.wiki.gallery",
    "pnpdistribution.com", "theexchange.com", "cdn11.bigcommerce.com", "images-na.ssl-images-amazon.com",
}
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
        if str(p.get("project_type") or "").strip().casefold() != "decompilation":
            continue
        url = str(p.get("repository_url") or "").strip()
        names = p.get("platforms") or ([p["platform"]] if p.get("platform") else [])
        platform = next((PLATFORMS[n.lower()] for n in names if isinstance(n, str) and n.lower() in PLATFORMS), "")
        if not url.startswith("https://") or not platform:
            continue
        hashes = p.get("supported_rom_sha1")
        sha1s = []
        if isinstance(hashes, list):
            sha1s = sorted({h.lower() for h in hashes if isinstance(h, str) and re.fullmatch(r"[0-9a-fA-F]{40}", h)})
        act = p.get("activity") or {}
        out.append({"id": key, "name": str(p.get("name") or key), "repo_url": url, "platform": platform,
                    "platform_name": consoles.name(platform),
                    "status": act.get("status") or p.get("legacy_status") or "",
                    "last_commit_at": act.get("last_commit_at") or "", "stars": act.get("github_stars"),
                    "archived": bool(act.get("archived")), "completion": p.get("completion_status"),
                    "ai_used": p.get("ai_used"), "rom_sha1s": sha1s})
    out.sort(key=lambda p: p["last_commit_at"], reverse=True)
    return out



def _repo_key(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")


class _Artwork(HTMLParser):
    """The illustrated directory groups a game's projects in the first two cells of each row."""

    def __init__(self):
        super().__init__()
        self.urls = {}
        self._cell = 0
        self._image = ""
        self._in_artwork = False
        self._repos = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._cell = 0
            self._image = ""
            self._repos.clear()
        elif tag == "td":
            self._cell += 1
        elif tag == "div" and self._cell == 1 and "rr-decomp-artwork" in (attrs.get("class") or "").split():
            self._in_artwork = True
        elif tag == "img" and self._in_artwork and attrs.get("src"):
            try:
                url = urljoin(DIRECTORY_PAGE, attrs["src"])
                parts = urlsplit(url)
                if parts.scheme == "https" and parts.netloc in ARTWORK_HOSTS:
                    self._image = url
            except ValueError:
                pass
        elif tag == "a" and self._cell in (1, 2) and attrs.get("href"):
            self._repos.add(_repo_key(attrs["href"]))

    def handle_endtag(self, tag):
        if tag == "div" or tag == "td":
            self._in_artwork = False
        elif tag == "tr" and self._image:
            for repo in self._repos:
                self.urls.setdefault(repo, self._image)


def artwork(html: str) -> dict[str, str]:
    parser = _Artwork()
    parser.feed(html)
    return parser.urls


def listing() -> list[dict]:
    try:
        r = httpx.get(DIRECTORY, timeout=30, follow_redirects=True)
        r.raise_for_status()
        projects = parse(r.json())
    except (httpx.HTTPError, ValueError) as e:
        raise NetworkError(f"Couldn't read the community decomp directory ({e}). Check your connection, "
                           "or paste a repository's address instead.") from e

    # The JSON contains repository metadata. The illustrated page supplies artwork, where available.
    try:
        r = httpx.get(DIRECTORY_PAGE, timeout=5, follow_redirects=True)
        r.raise_for_status()
        images = artwork(r.text)
    except (httpx.HTTPError, ValueError):
        return projects
    for project in projects:
        if image := images.get(_repo_key(project["repo_url"])):
            project["artwork_url"] = image
    return projects
