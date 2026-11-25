"""Do the port's pictures match the console's? (Check pictures, Porting tab; docs/PORTING.md, "Comparing pictures")

The reversing emulator draws nothing, so a port can't be compared with it by a screenshot. What it can do is write the
console's RAM as the game hands over a graphics task. This runs the user's ROM in it and the port's program through the
same controller script, takes both at chosen graphics tasks, and draws each with the port's renderer (port/frame_compare.py,
the one engine of tools/n64_frame_compare.py): a difference is the game running differently, never the renderer. The port
starts from the console's own save and replays the console's pacing, the two things that made a racing game's port equal
the emulator's to the pixel. Results and the side-by-side images (console, port, their difference) go in the project's
build folder (derived from the ROM: git ignores it)."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from ..network import NetworkError

POINTS = 4  # windows compared, spread over the run
TOLERANCE = 3  # pixels a picture may be shifted before a pixel counts as different (a camera caught a frame earlier)


def folder(root: Path) -> Path:
    return Path(root) / "build" / "pictures"


def results_file(root: Path) -> Path:
    return folder(root) / "results.json"


WINDOW = 24  # consecutive tasks taken at each moment: the first half only rebuilds what the buffers held (frame_compare.compare_run)


def points(tasks: int, count: int = POINTS) -> list[int]:
    """`count` moments spread from the 600th graphics task (the game has read its save by then) to `tasks`, each a window
    of WINDOW consecutive tasks: a game draws a picture over several tasks, in two buffers taking turns, and the emulator
    draws none of them, so a buffer's picture is only whole once the window has drawn it from its first task (on a menu
    route a single task showed 63% different where, with the tasks before it drawn too, the picture matched to the pixel)."""
    tasks = max(int(tasks), WINDOW + 1)
    first = min(600, tasks - WINDOW)
    step = max((tasks - WINDOW - first) // max(count - 1, 1), 1)
    out: set[int] = set()
    for i in range(count):
        n = min(first + i * step, tasks - WINDOW)
        out.update(range(n, n + WINDOW))
    return sorted(out)


def seeding_function(repo: Path) -> str | None:
    """The name of the game's seeding function, when the port has an override of it that takes its seed through
    port_seed_replay (port/wasm/src, docs/PORTING.md): `void rand_seed(s32 seed) { ... port_seed_replay(...) }`. A game that
    seeds its random numbers from the clock draws other numbers in the port, so a demo race or a spawn differs entirely
    unless the port is given the console's seeds."""
    import re
    for source in sorted((Path(repo) / "port" / "wasm" / "src").glob("*.c")):
        text = source.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\b(\w+)\s*\([^)]*\)\s*\{[^{}]*port_seed_replay\s*\(", text):
            return m.group(1)
    return None


def stalled(results: list[dict]) -> str | None:
    """A sentence when the port never reached most of the moments to compare (it stopped, hung or stalled: replaying the console's seeds
    has left a game at a few hundred of its thousand graphics tasks), None when the run is worth keeping: a run that compares nothing must
    not read as a port with nothing differing."""
    moments = {r["task"] for r in results}
    gone = {r["task"] for r in results if r.get("missing") == "port"}
    compared = [r for r in results if "differ_pct" in r]
    if moments and (len(gone) * 2 > len(moments) or not compared and gone):
        return (f"The port never reached {len(gone)} of the {len(moments)} frames to compare, so no result is kept: it stopped or stalled before them. "
                "Run it again; if it repeats, try a shorter run or another controller script.")
    return None


def check(net, slug: str, tasks: int = 6000, inputs: str = "", tolerance: int = TOLERANCE, pace: bool = True,
          log=print) -> dict:
    """Run the comparison (a background job): the summary, saved in results_file."""
    from ..port import frame_compare
    from ..port.wasm import build, play
    with net.progress.view():
        proj = net.project_row(slug)
        console = net.console(proj)
    if console.emulator is None or not net.is_rom(proj):
        raise NetworkError(f"{console.name} has no reversing emulator in decompdecomp yet: pictures are checked on N64 "
                           "games for now")
    if console.emulator.installed() is None:
        raise NetworkError("The reversing emulator isn't installed: install mupenRE under Compilers & tools → Tools.")
    stop = getattr(log, "cancel_event", None) or threading.Event()
    report = getattr(log, "progress", None) or (lambda value: None)
    root = Path(proj["root"])
    out = folder(root)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    report({"phase": "Building the port", "step": 1, "steps": 3})
    repo, cfg, rom = play.game(net, slug)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, **play.inputs(net, slug, log))
    chosen = points(tasks)
    seeds_at = None
    if (name := seeding_function(repo)) is not None:
        with net.progress.view():
            row = net.project_store(proj["id"]).one("SELECT vram FROM functions WHERE project_id=? AND name=?", proj["id"], name)
        if row is not None and row["vram"]:
            seeds_at = int(row["vram"]) & 0xFFFFFFFF
            log(f"the console's seeds for {name} (0x{seeds_at:08X}) are replayed in the port")
    work = out / "work"

    def progress(text: str, done: int, total: int) -> None:
        log(text)
        report({"phase": text, "step": 2, "steps": 3, "done": done, "total": total})

    try:
        results = frame_compare.compare_run(program, rom, chosen, inputs.strip(), work, out, tolerance=int(tolerance),
                                            pace=pace, seeds_at=seeds_at, stop=stop.is_set, progress=progress)
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the RAM dumps: 8 MB each
    if stop.is_set():
        raise NetworkError("Stopped: no pictures were kept for this run.")
    unreached = stalled(results)
    if unreached:
        raise NetworkError(unreached)
    saved = {"at": time.time(), "settings": {"tasks": int(tasks), "input": inputs.strip(), "tolerance": int(tolerance),
                                             "pace": bool(pace)}, "pictures": results}
    tmp = results_file(root).with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    os.replace(tmp, results_file(root))
    counts = summary(root) or {}
    log(f"{counts.get('same', 0)} of {counts.get('compared', 0)} pictures match the console's, "
        f"{counts.get('differ', 0)} differ")
    return counts


def summary(root: Path) -> dict | None:
    """The last Check pictures, counted (Port readiness): None before one ran. A picture matches when no pixel differs,
    or none has no equal pixel within the tolerance (a scene a frame apart); `differing` lists the rest, worst first."""
    path = results_file(root)
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None
    if not saved:
        return None
    pictures = saved.get("pictures", [])
    compared = [p for p in pictures if "differ_pct" in p]

    def worst(p: dict) -> float:
        return p["near_pct"] if p.get("near_pct") is not None else p["differ_pct"]
    exact = [p for p in compared if p["differ_pct"] == 0]
    near = [p for p in compared if p["differ_pct"] > 0 and worst(p) == 0]
    differing = sorted((p for p in compared if worst(p) > 0), key=worst, reverse=True)
    missing = [p for p in pictures if "missing" in p]
    return {"at": saved.get("at"), "settings": saved.get("settings"), "compared": len(compared), "exact": len(exact),
            "shifted": len(near), "same": len(exact) + len(near), "differ": len(differing), "missing": len(missing),
            "differing": [{"task": p["task"], "image": p["image"], "differ_pct": p["differ_pct"],
                           "near_pct": p.get("near_pct"), "file": p["file"]} for p in differing[:40]],
            "examples": [f"frame {p['task']}: {p['differ_pct']:.2f}% of pixels differ"
                         + (f", {p['near_pct']:.2f}% with no match within {saved['settings']['tolerance']} px"
                            if p.get("near_pct") is not None else "") for p in differing[:6]]}


def image_path(root: Path, name: str) -> Path | None:
    """A side-by-side picture of the last run, by its file name (nothing outside the pictures folder)."""
    path = folder(root) / Path(name).name
    return path if path.suffix == ".png" and path.is_file() else None
