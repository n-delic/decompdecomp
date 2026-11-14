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
MIN_SAMPLE = 16  # functions needed before their common alignment means the compiler's padding


def _compiler_at(vram: int, ranges: list[dict]) -> tuple:
    for r in ranges:
        if r["start"] <= vram < r["end"]:
            return (r.get("toolchain"), tuple(r.get("flags") or ()))
    return (None, ())


def function_alignment(starts: list[int]) -> int:
    """What every function is padded to by its compiler: the largest of 8..64 nearly all starts are multiples of,
    else 4. EE-GCC starts each PS2 function on 8 bytes, so its 4-byte gaps say nothing about files (the PS2 demo:
    49 such gaps, 18 real files); KMC GCC on the N64 pads only objects."""
    if len(starts) < MIN_SAMPLE:  # a handful of functions can share any alignment by chance
        return 4
    for k in (64, 32, 16, 8):
        if sum(s % k == 0 for s in starts) >= 0.98 * len(starts):
            return k
    return 4


def guess_units(functions: list[dict], ranges: list[dict] | None = None) -> list[dict]:
    """Group functions (dicts with name, vram, size, segment, status) into guessed source files, in address order.

    A new file starts where a segment changes, where the project's compiler ranges change compiler, or after a
    gap of padding that ends on a file-aligned address and that the compiler's own function alignment doesn't
    explain (an object's .text is padded to 16 bytes, a function's end within one object is not). Neighbouring
    files with no padding between them stay together: nothing in the bytes tells them apart (the PS1 demo's 18
    files have none), so only matching and the agents' joins and splits can."""
    ranges = sorted(ranges or [], key=lambda r: r["start"])
    align = function_alignment([f["vram"] for f in functions])
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
        elif (end := prev["vram"] + prev["size"]) < fn["vram"] and fn["vram"] % FILE_ALIGN == 0 \
                and align < FILE_ALIGN and fn["vram"] != -(-end // align) * align:
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


def group_objdiff(folder: Path, units: list[dict]) -> int:
    """Give the export's objdiff.json one progress category per guessed file, so objdiff shows each file's progress.
    The export writes one objdiff unit per function, named "<segment>/<function>"; the categories group them.
    Returns how many objdiff units were placed in a file."""
    path = folder / "objdiff.json"
    if not path.is_file():
        return 0
    config = json.loads(path.read_text(encoding="utf-8"))
    owner = {name: u["name"] for u in units for name in u["functions"]}
    placed = 0
    for unit in config.get("units", []):
        file = owner.get(unit["name"].rsplit("/", 1)[-1])
        if file:
            meta = unit.setdefault("metadata", {})
            meta["progress_categories"] = [file]
            placed += 1
    config["progress_categories"] = [{"id": u["name"], "name": u["name"] + (" (guessed)" if u["provisional"] else "")}
                                     for u in units]
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return placed


def build_targets(folder: Path, log=print) -> bool:
    """Assemble each function's target object (`build.py targets`), so the folder opens in objdiff as it is.
    False (and the reason logged) when that fails: the folder is still right, objdiff just has to wait for it."""
    from ..hostenv import console_python
    from .export_process import run
    proc = run([console_python(), "build.py", "targets"], folder, log)
    if proc.returncode != 0:
        log("objdiff's targets weren't built: " + (proc.stdout + proc.stderr)[-800:])
    return proc.returncode == 0


def summary(units: list[dict], folder: Path) -> dict:
    return {"t": time.time(), "path": str(folder), "units": len(units),
            "confirmed": sum(not u["provisional"] for u in units)}
