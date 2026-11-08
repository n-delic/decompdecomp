"""Offline PS1/PS2 display names; serials remain the identity, never evidence of matching bytes."""
import json
import re
from functools import cache
from pathlib import Path


@cache
def _titles(platform):
    return json.loads(Path(__file__).with_name(f"{platform}.json").read_text(encoding="utf-8"))


def title(platform, code, fallback):
    return _titles(platform).get(code, fallback)


def disc_title(platform, info, image):
    """Known serial first, otherwise a useful volume label. Generic mastering labels aren't titles."""
    label = image.sector(16)[40:72].decode("ascii", "replace").strip(" \0")
    generic = {"CDROM", "CD_ROM", "DVDROM", "DVD_ROM", "UNTITLED", "PLAYSTATION", "PLAYSTATION2", "PSX", "PS2"}
    serial = re.fullmatch(r"[A-Z]{4}[-_]?[0-9]{3}\.?[0-9]{2}", label, re.I)
    if (not label or label.upper() in generic or serial or not any(c.isalpha() for c in label)
            or any(not c.isprintable() or c == "\ufffd" for c in label)):
        label = info["title"]
    else:
        label = label.replace("_", " ")
    info["title"] = title(platform, info["game_code"], label)
