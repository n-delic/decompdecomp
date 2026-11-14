"""A ROM project laid out as a project folder from the start: guessed units beside the rebuildable export.

A stripped ROM doesn't say where its source files began, so the units here are guesses, each marked provisional
with the reason it starts where it does. The ROM database stays the truth and its judge stays the one that links
each function at its real address: the folder is regenerated from it (rom/export.py), never edited into it.
A unit becomes confirmed once every function in it matches.

Agents still work through ROM mode. Making these units editable in project mode (edits judged back through
rom/match.py, units regrouped by joins and splits) is a ROADMAP item: "ROM projects as project folders".
"""
from __future__ import annotations

import json
import time
from pathlib import Path

UNITS_FILE = "units.json"
FILE_ALIGN = 16  # MIPS GCC and its assemblers start each object's .text on a 16-byte boundary


def _compiler_at(vram: int, ranges: list[dict]) -> tuple:
    for r in ranges:
        if r["start"] <= vram < r["end"]:
            return (r.get("toolchain"), tuple(r.get("flags") or ()))
    return (None, ())


def guess_units(functions: list[dict], ranges: list[dict] | None = None) -> list[dict]:
    """Group functions (dicts with name, vram, size, segment, status) into guessed source files, in address order.

    A new file starts where a segment changes, where the project's compiler ranges change compiler, or after a
    gap of padding that ends on a file-aligned address (an object's .text is padded to 16 bytes, a function's end
    within one object is not). Neighbouring files with no padding between them stay together: nothing in the
    bytes tells them apart, so only matching and the agents' joins and splits can."""
    ranges = sorted(ranges or [], key=lambda r: r["start"])
    units: list[dict] = []
    prev = None
    for fn in sorted(functions, key=lambda f: f["vram"]):
        why = None
        if prev is None:
            why = "start"
        elif fn["segment"] != prev["segment"]:
            why = "segment"
        elif _compiler_at(fn["vram"], ranges) != _compiler_at(prev["vram"], ranges):
            why = "compiler"
        elif fn["vram"] > prev["vram"] + prev["size"] and fn["vram"] % FILE_ALIGN == 0:
            why = "padding"
        if why:
            units.append({"name": f"{fn['segment']}_{fn['vram']:08X}", "segment": fn["segment"],
                          "start": fn["vram"], "end": fn["vram"] + fn["size"], "why": why, "functions": []})
        unit = units[-1]
        unit["functions"].append(fn["name"])
        unit["end"] = max(unit["end"], fn["vram"] + fn["size"])
        unit.setdefault("_status", []).append(fn.get("status"))
        prev = fn
    for unit in units:
        statuses = unit.pop("_status")
        unit["matched"] = sum(s == "matched" for s in statuses)
        unit["provisional"] = unit["matched"] < len(statuses)
    return units


def write_units(folder: Path, units: list[dict]) -> Path:
    """units.json in the project folder: the guessed files, read by people and, later, the project adapter."""
    path = folder / UNITS_FILE
    path.write_text(json.dumps({
        "about": "Guessed source files of this game. Provisional units start where the bytes suggest a file "
                 "boundary (why: segment, compiler, padding) and may move as functions match.",
        "units": [dict(u, start=f"0x{u['start']:08X}", end=f"0x{u['end']:08X}") for u in units],
    }, indent=1) + "\n", encoding="utf-8")
    return path


def summary(units: list[dict], folder: Path) -> dict:
    return {"t": time.time(), "path": str(folder), "units": len(units),
            "confirmed": sum(not u["provisional"] for u in units)}
