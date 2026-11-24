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

POINTS = 12  # windows compared, spread over the run
TOLERANCE = 3  # pixels a picture may be shifted before a pixel counts as different (a camera caught a frame earlier)


def folder(root: Path) -> Path:
    return Path(root) / "build" / "pictures"


def results_file(root: Path) -> Path:
    return folder(root) / "results.json"


def points(tasks: int, count: int = POINTS) -> list[int]:
    """`count` graphics tasks spread from the 600th (the game has read its save by then) to `tasks`, each followed by
    its next one: a game draws a picture over several tasks, so a window of two catches the one the screen shows."""
    tasks = max(int(tasks), 2)
    first = min(600, tasks - 1)
    step = max((tasks - first) // max(count - 1, 1), 1)
    out: list[int] = []
    for i in range(count):
        n = min(first + i * step, tasks - 1)
        out += [n, n + 1] if not out or out[-1] != n else [n + 1]
    return sorted(set(out))


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
    work = out / "work"

    def progress(text: str, done: int, total: int) -> None:
        log(text)
        report({"phase": text, "step": 2, "steps": 3, "done": done, "total": total})

    try:
        results = frame_compare.compare_run(program, rom, chosen, inputs.strip(), work, out, tolerance=int(tolerance),
                                            pace=pace, stop=stop.is_set, progress=progress)
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the RAM dumps: 8 MB each
    if stop.is_set():
        raise NetworkError("Stopped: no pictures were kept for this run.")
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
