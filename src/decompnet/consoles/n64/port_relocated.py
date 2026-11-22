"""Recognize relocated SDK functions by validated loader relocations and exact SDK fingerprints."""
from __future__ import annotations

import hashlib
import struct
from collections import defaultdict
from pathlib import Path

from ...elfobj import Reloc, mask_relocs
from . import uso


def _table() -> Path:
    return Path(__file__).with_name("ultralib_signatures.tsv")


def patterns(image: bytes, hosted: set[str]) -> list[tuple[str, tuple[int, ...], tuple[int, ...]]]:
    """Masked instruction patterns for uniquely identified SDK functions in validated USO modules.

    An imported SDK fingerprint contains the whole function and its relocation layout. The USO loader supplies
    that layout independently of its eventual load address. Ambiguous identities and short bodies are excluded.
    """
    index = defaultdict(set)
    table = _table()
    for line in table.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fingerprint, size, name, *_ = line.split("\t")
        index[(int(size), fingerprint)].add(name)
    sizes = sorted({size for (size, _), names in index.items() if size >= 32 and size % 4 == 0 and len(names) == 1
                    and next(iter(names)) in hosted})
    found = defaultdict(set)
    for region in uso.read(image):
        if not region.relocations:
            continue
        for entry in region.entries:
            for size in sizes:
                if entry + size > len(region.data):
                    break
                relocations = uso.function_relocations(region, entry, size)
                if relocations is None:
                    continue
                layout = [(at, kind) for at, kind, _ in relocations]
                reloc = [Reloc(at, kind, "", 0) for at, kind in layout]
                raw = region.data[entry:entry + size]
                masked = mask_relocs(raw, reloc, "mips", True)
                digest = hashlib.sha1(masked)
                digest.update(repr(layout).encode())
                names = index.get((size, digest.hexdigest()), set())
                if len(names) != 1 or (name := next(iter(names))) not in hosted:
                    continue
                mask = mask_relocs(b"\xff" * size, reloc, "mips", True)
                if sum(byte.bit_count() for byte in mask) < 128:
                    continue
                words = struct.unpack(f">{size // 4}I", masked)
                masks = struct.unpack(f">{size // 4}I", mask)
                found[(words, masks)].add(name)
    return [(next(iter(names)), words, masks) for (words, masks), names in found.items() if len(names) == 1]
