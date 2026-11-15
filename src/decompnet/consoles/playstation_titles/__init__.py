"""PS1/PS2 display names from libretro-database's Redump lists; serials remain the identity, never evidence of
matching bytes.

The lists (CC BY-SA 4.0) are a pinned download (`playstation-titles` in toolchains.toml), not part of the app: 21,000
lines of generated JSON made every pull request that touched them huge. They're fetched the first time a PS1 or PS2
game is imported; without them (offline, or the download failed) a disc keeps its volume label or serial, and
nothing else changes. Showing a title never touches the network: only an import does.
"""
import json
import os
import re
import threading
from pathlib import Path

TOOL = "playstation-titles"
DATS = {"ps": "ps.dat", "ps2": "ps2.dat"}
_cache: dict[str, dict[str, str]] = {}
_lock = threading.Lock()
_tried = False


def extract(text):
    """{serial: title} from a Redump .dat. Revisions of one release share its title; a serial two different
    games use names neither."""
    candidates = {}
    for block in re.split(r"\n?game \(\n", text)[1:]:
        name = re.search(r'^\s*name "([^"]+)"', block, re.M)
        serial = re.search(r'^\s*serial "([^"]+)"', block, re.M)
        if not name or not serial:
            continue
        title = re.sub(r" \((?:Rev|v) [^)]*\)", "", name[1])
        for code in re.findall(r"[A-Z]{4}-\d{5}(?![\d-])", serial[1]):
            candidates.setdefault(code, set()).add(title)
    return {code: next(iter(names)) for code, names in sorted(candidates.items()) if len(names) == 1}


def _folder() -> Path | None:
    """Where the lists are: $DECOMPNET_PLAYSTATION_TITLES (tests, or a copy the user keeps), else the installed pack."""
    if override := os.environ.get("DECOMPNET_PLAYSTATION_TITLES"):
        return Path(override)
    from ... import toolchains

    return toolchains.path(TOOL) if toolchains.installed(TOOL) else None


def _titles(platform) -> dict[str, str]:
    if platform in _cache:
        return _cache[platform]
    folder = _folder()
    if folder is None:
        return {}  # not cached: an import may download the lists later in this run
    dat, parsed = folder / DATS[platform], folder / f"{platform}.json"  # parsing the .dat: once per install
    try:
        titles = json.loads(parsed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if not dat.is_file():
            return {}
        titles = extract(dat.read_text(encoding="utf-8", errors="replace"))
        try:
            parsed.write_text(json.dumps(titles, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    _cache[platform] = titles
    return titles


def fetch() -> None:
    """Download the lists once per run if they're missing; a failure (offline) leaves titles as they are."""
    global _tried
    with _lock:
        if _tried or os.environ.get("DECOMPNET_PLAYSTATION_TITLES"):
            return
        _tried = True
    from ... import toolchains

    try:
        toolchains.ensure(TOOL, log=lambda *_: None)
    except Exception:  # noqa: BLE001 - a display name never stops an import
        pass


def title(platform, code, fallback):
    return _titles(platform).get(code, fallback)


def import_title(platform, code, fallback):
    """`title` for an import: the one place the lists may be downloaded."""
    fetch()
    return title(platform, code, fallback)


def disc_title(platform, info, image):
    """Known serial first, otherwise a useful volume label. Generic mastering labels aren't titles."""
    label = image.sector(16)[40:72].decode("ascii", "replace").strip(" \0")
    generic = {"CDROM", "CD_ROM", "DVDROM", "DVD_ROM", "UNTITLED", "PLAYSTATION", "PLAYSTATION2", "PSX", "PS2"}
    serial = re.fullmatch(r"[A-Z]{4}[-_]?[0-9]{3}\.?[0-9]{2}", label, re.I)
    if (not label or label.upper() in generic or serial or not any(c.isalpha() for c in label)
            or any(not c.isprintable() or c == "�" for c in label)):
        label = info["title"]
    else:
        label = label.replace("_", " ")
    info["title"] = import_title(platform, info["game_code"], label)
