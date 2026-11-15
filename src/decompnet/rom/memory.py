"""Additional original-image regions for functions in overlays whose data was omitted at import.

The local manifest is an analysis input, like splat's configuration. Its owner is a segment name, not a
RAM address: two overlays can put different strings at the same address. Payloads stay outside git.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..network import NetworkError


def overlay_reader(root: Path, image_sha1: str, owner: str):
    """Return a reader of an owner's verified local data image, or None when none was supplied."""
    manifest = root / "memory_regions.json"
    if not manifest.exists():
        return None
    try:
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        if spec["format"] != 1 or spec["image_sha1"] != image_sha1:
            raise ValueError("the memory regions describe another analysis image")
        payload = (root / spec["file"]).resolve()
        if not payload.is_relative_to(root.resolve()):
            raise ValueError("the memory-region payload must be inside the project")
        data = payload.read_bytes()
        if hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError("the memory-region payload checksum changed")
        regions = [r for r in spec["regions"] if r["owner"] == owner]
        for region in regions:
            if min(region["offset"], region["size"], region["vram"]) < 0 or region["offset"] + region["size"] > len(data):
                raise ValueError("a memory region is outside its payload")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NetworkError(f"Cannot read memory_regions.json: {exc}") from exc

    def read(address: int, size: int):
        for region in regions:
            at = address - region["vram"]
            if 0 <= at and at + size <= region["size"]:
                offset = region["offset"] + at
                return data[offset:offset + size]
        return None
    return read
