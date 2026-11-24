"""Scan for rendering faults (Porting tab): run the port headless on the user's own ROM with a scripted controller,
save the frames it shows, and look through them (port/frame_scan.py) for the ones that look wrong: static, a flat
colour, a picture streaked sideways (a texture read with the wrong stride), a frame with only its top drawn.

It needs no emulator: the port's own frames are judged by what a picture can't look like. A finding names the graphics
task it came after, which tools/n64_frame_compare.py --frames draws on the console's side. The frames themselves are
deleted after the scan (a minute of them is hundreds of megabytes); the few flagged ones are kept, small, in the result
so the drawer can show them. Local CPU, no AI tokens, nothing leaves the machine."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..network import NetworkError
from . import frame_scan

KEPT = 12  # flagged frames whose pictures are kept in the result: the rest are listed without one
KINDS = {"noisy": "looks like static", "flat": "one flat colour that isn't black",
         "smeared": "streaked sideways: a texture read with the wrong stride or width",
         "partial": "only the top is drawn, the rest black"}


def results_file(root: Path) -> Path:
    return Path(root) / "build" / "render-scan" / "last.json"


def summary(root: Path) -> dict | None:
    """The last scan's result (Port readiness): None before one ran."""
    path = results_file(root)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None


def findings(found: dict) -> list[dict]:
    """One row per flagged frame, from frame_scan.scan's answer, with every reason it was flagged."""
    rows: dict[int, dict] = {}

    def add(frame, kind, detail, task):
        row = rows.setdefault(frame, {"frame": frame, "task": task, "kinds": [], "details": []})
        row["kinds"].append(kind)
        row["details"].append(f"{KINDS[kind]} ({detail})" if detail else KINDS[kind])
    for frame, noise, task in found["noisy"]:
        add(frame, "noisy", f"noise {noise}", task)
    for frame, task in found["flat"]:
        add(frame, "flat", "", task)
    for frame, ratio, task in found["smeared"]:
        add(frame, "smeared", f"{ratio}x more change down than across", task)
    for frame, share, task in found["partial"]:
        add(frame, "partial", f"{share:.0%} of the height", task)
    return [rows[n] for n in sorted(rows)]


def _picture(path: Path) -> str | None:
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def run(program: Path, rom: Path, out: Path, *, seconds: int, every: int, input: str, log=print,
        env: dict | None = None) -> Path:
    """Run the port headless for `seconds` of the game's own time, saving every `every`th frame under out/frames."""
    frames = out / "frames"
    shutil.rmtree(out, ignore_errors=True)
    frames.mkdir(parents=True)
    environment = {**os.environ, **(env or {}), "PORT_FRAMES": str(frames), "PORT_FRAME_EVERY": str(every),
                   "PORT_SWAP_LOG": str(out / "swaps.log"), "PORT_VTIME": "1"}
    for key in ("PORT_LIVE", "PORT_WINDOW", "PORT_RT64"):
        environment.pop(key, None)
    if input.strip():
        environment["PORT_INPUT"] = input.strip().replace("\n", ",")
        environment["PORT_INPUT_CLOCK"] = "vi"
    stop = getattr(log, "cancel_event", None) or threading.Event()
    with (out / "run.log").open("w", encoding="utf-8") as run_log:
        proc = subprocess.Popen([str(program), str(rom), str(seconds)], cwd=program.parent, env=environment,
                                stdout=run_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    started, deadline = time.monotonic(), time.monotonic() + max(120, seconds * 12)
    try:
        while proc.poll() is None:
            getattr(log, "check_cancel", lambda: None)()
            if stop.is_set():
                raise NetworkError("The scan was stopped.")
            if time.monotonic() > deadline:
                raise NetworkError(f"The game didn't finish {seconds} seconds in {deadline - started:.0f} s: see "
                                   f"{out / 'run.log'}")
            shown = sum(1 for _ in frames.glob("frame_*.png"))
            getattr(log, "progress", lambda *a, **k: None)(
                {"phase": "Running the game", "step": 1, "steps": 2, "done": min(shown * every, seconds * 60),
                 "total": seconds * 60})
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if proc.returncode:
        raise NetworkError(f"The game stopped with status {proc.returncode} before the scan finished: see "
                           f"{out / 'run.log'}")
    return frames


def scan(net, slug: str, *, seconds: int = 60, every: int = 6, input: str = "", log=print) -> dict:
    """Build the port if anything changed, run it headless, look through its frames and save the result."""
    from .wasm import build, play

    repo, cfg, rom = play.game(net, slug)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, **play.inputs(net, slug, log))
    root = Path(net.project_row(slug, sync=False)["root"])
    out = results_file(root).parent
    log(f"Running the game for {seconds} seconds, saving every {every}th frame.")
    env = {"PORT_SAVE": str(out / "game.eep"), "PORT_SRAM": str(out / "game.sra"), "PORT_FLASH": str(out / "game.fla")}
    if memory := (cfg.get("rom") or {}).get("memory_size"):
        env["PORT_MEMSIZE"] = str(memory)
    frames = run(program, rom, out, seconds=seconds, every=every, input=input, log=log, env=env)
    found = frame_scan.scan(frames, out / "swaps.log")
    rows = findings(found)
    for row in rows[:KEPT]:
        row["picture"] = _picture(frames / f"frame_{row['frame']:05d}.png")
    result = {"at": time.time(), "settings": {"seconds": seconds, "every": every, "input": input},
              "frames": found["frames"], "black": found["black"], "findings": rows}
    shutil.rmtree(frames, ignore_errors=True)
    results_file(root).write_text(json.dumps(result), encoding="utf-8")
    log(f"{found['frames']} frames looked through: {len(rows)} look wrong." if rows
        else f"{found['frames']} frames looked through: none look wrong.")
    return {k: v for k, v in result.items() if k != "findings"} | {"flagged": len(rows)}
