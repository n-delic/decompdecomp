"""Throughput benchmark: functions matched per CPU-minute on the demo games, by the machine-only pipeline.

Performance work used to be judged by latency (a faster compile, a cache hit), which says nothing about whether
more functions match for the same CPU. This runs what a user's "Run autopilot" click runs (twins, machine drafts
through the judge, the deterministic saved-source repairs; then, with `--permuter`, the background permuter on the
near misses left) on each demo game whose tools are installed, for a fixed wall-clock budget, and reports the
outcome: functions matched before and after, matches per CPU-minute, and how the compile pipeline spent its time
(compile_timing's counters). No helper agent is started: nothing here costs a token.

Everything happens in a throwaway home it makes itself (DECOMP_HOME, no window, no tray, no agent detection), so the
user's own app and projects are never touched. The demos build into their gitignored `projects/<demo>/build/`, as
tests/test_consoles.py builds them.

    uv run python -m decompnet.bench                       # every demo with its tools, 120 s each
    uv run python -m decompnet.bench --demo ps --seconds 30 --out before.json
    uv run python -m decompnet.bench --compare before.json after.json

The CPU time is this process's plus its children's that have exited (os.times): compilers, assemblers and the m2c
workers, which autopilot stops when its lanes finish. It can't see a compiler that runs in the Linux VM or WSL, nor
children on Windows: `cpu_seconds.complete` is false then, and only the wall-clock figures compare. A project-mode
demo (GameCube) builds through its own build system, not compiler.py, so its compile_timing counters stay empty. A run is repeatable on one machine, not across machines:
compare a before and an after made on the same computer.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT / "projects"
SECONDS = 120  # default budget per demo, wall clock
POLL_SECONDS = 0.25  # how often a running phase's state is read
STOP_GRACE_SECONDS = 60  # how long a stopped phase may take to finish the compiles it already started
BUILD_SECONDS = 600  # a demo's own build script (compiles the whole Demo Quest game and zlib)
FORMAT = 1  # the JSON's layout version: --compare refuses a different one

# compile_timing counters worth a line in --compare (seconds unless named *_count); the JSON keeps them all
TIMING_LINES = ("requests", "cache_hits", "invocations", "queue", "setup", "execute", "link", "scoring", "generation")


def isolate(home: Path) -> None:
    """Point everything the app reads at a throwaway home, before any app module is imported: the user's own app
    runs with agents working in it (AGENTS.md, "Never disturb the user's own decompdecomp")."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["DECOMP_HOME"] = str(home)
    for name, value in (("DECOMP_WINDOW", "0"), ("DECOMP_TRAY", "0"), ("DECOMP_DETECT_AGENTS", "off"),
                        ("DECOMP_UPDATES", "off"), ("DECOMP_MCP_GATEWAY", "0")):
        os.environ[name] = value


def missing_tools(console) -> list[str]:
    """What the console's demo needs and this computer hasn't got (toolchains, or programs on PATH)."""
    from . import toolchains

    def there(tool: str) -> bool:
        try:
            toolchains.get(tool)
            return toolchains.installed(tool) is not None
        except toolchains.ToolchainError:  # a program on PATH, not a toolchain
            return shutil.which(tool) is not None
    missing = [tool for tool in console.demo_tools if not there(tool)]
    if not (PROJECTS / "zlib-n64" / "original").exists():  # every demo's build compiles zlib too
        missing.append("zlib (projects/zlib-n64/prepare.py)")
    return missing


def demos(wanted: list[str] | None = None) -> list[tuple[object, str]]:
    """(console, why it's skipped or "") for each console with a demo game, or only the `wanted` ids."""
    from . import consoles

    chosen = [c for c in consoles.startable() if c.demo]
    if wanted:
        known = {key: c for c in chosen for key in (c.id, *c.aliases, c.demo)}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            raise SystemExit(f"no demo for {', '.join(unknown)}: choose from {', '.join(sorted(c.id for c in chosen))}")
        chosen = list(dict.fromkeys(known[w] for w in wanted))
    out = []
    for console in chosen:
        missing = missing_tools(console)
        out.append((console, f"needs {', '.join(missing)}" if missing else ""))
    return out


def build(console) -> Path:
    """The demo game's file, built by its own script when it isn't there yet."""
    folder = PROJECTS / console.demo
    key = folder / "build" / "demo.json"
    if not key.exists():
        subprocess.run([sys.executable, str(folder / "build.py")], check=True, timeout=BUILD_SECONDS,
                       capture_output=True)
    return folder / "build" / json.loads(key.read_text())["game"]


def progress(net, slug: str) -> dict:
    """The project's totals as the GUI shows them (project_totals, kept by the store's triggers)."""
    proj = net.project_row(slug)
    row = net.project_store(proj["id"]).one(
        "SELECT total, matched, set_aside, bytes, matched_bytes FROM project_totals WHERE project_id=?", proj["id"])
    row = dict(row) if row else dict.fromkeys(("total", "matched", "set_aside", "bytes", "matched_bytes"), 0)
    return {"functions": row["total"] or 0, "matched": row["matched"] or 0, "set_aside": row["set_aside"] or 0,
            "bytes": row["bytes"] or 0, "matched_bytes": row["matched_bytes"] or 0}


def _cpu() -> tuple[float, float]:
    """(this process's CPU seconds, its exited children's). Windows doesn't count children: 0 there."""
    t = os.times()
    return t.user + t.system, t.children_user + t.children_system


def _delta(after: dict, before: dict) -> dict:
    """What the run added to each compile_timing counter. The gauges (compiles executing or waiting now) aren't
    totals: their difference is only what happened to be in flight at either end."""
    return {k: round(after.get(k, 0) - before.get(k, 0), 6) for k in sorted(set(after) | set(before))
            if after.get(k, 0) != before.get(k, 0) and k != "executing" and not k.startswith("waiting_")}


def _wait(running, stop, deadline: float) -> bool:
    """Poll `running()` until it's false or the deadline passes; then `stop()` and wait for the work in flight.
    True when the phase finished on its own inside the budget."""
    while running() and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    if not running():
        return True
    stop()
    grace = time.monotonic() + STOP_GRACE_SECONDS
    while running() and time.monotonic() < grace:
        time.sleep(POLL_SECONDS)
    return False


def run_autopilot(net, slug: str, deadline: float, workers: int | None) -> dict:
    """The app's "Run autopilot" (routes/projects.py: foreground=True), stopped at the deadline."""
    state = net.start_autopilot(slug, workers, foreground=True)
    finished = _wait(lambda: state["state"] == "running", lambda: net.stop_autopilot(slug), deadline)
    status = net.autopilot_status(slug) or {}
    return {"finished": finished, "no_drafts": status.get("no_drafts") or "",
            **{k: status.get(k, 0) for k in ("total", "tried", "matched", "near", "twins", "repair_total",
                                              "repair_done", "repair_matched")}}


def run_permuter(net, slug: str, deadline: float) -> dict:
    """The background permuter on the near misses autopilot left, until it has none left to try or the budget ends.
    (Left alone it would look again every few minutes for an hour, waiting for agents to make new ones.)"""
    proj = net.project_row(slug)
    if not net.permuter.candidates(proj):
        return {"finished": True, "candidates": 0}
    state = net.permuter.start(slug)
    if state is None:
        return {"finished": True, "candidates": 0}

    def running() -> bool:
        if state.state not in ("running", "update_wait"):
            return False
        with state._lock:
            busy = bool(state.current)
        return busy or bool(net.permuter.candidates(net.project_row(slug)))
    finished = _wait(running, lambda: net.permuter.stop(slug), deadline)
    net.permuter.stop(slug)
    net.permuter.join()
    status = net.permuter.status(slug) or {}
    return {"finished": finished, **{k: status.get(k, 0) for k in ("tried", "variants", "improved", "matched")}}


def bench_demo(console, work: Path, seconds: float, *, workers: int | None = None, permuter: bool = False) -> dict:
    """One demo: import it into a fresh project, run the machine-only pipeline for `seconds`, and measure."""
    from . import compile_timing
    from .app.engine import RomNetwork

    game = build(console)
    work.mkdir(parents=True, exist_ok=True)
    net = RomNetwork(work / "app.db", work)
    started = time.monotonic()
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    import_seconds = time.monotonic() - started
    before = progress(net, slug)
    timing_before = compile_timing.snapshot()
    cpu_self, cpu_children = _cpu()
    started = time.monotonic()
    deadline = started + seconds
    phases = {"autopilot": run_autopilot(net, slug, deadline, workers)}
    if permuter:
        phases["permuter"] = run_permuter(net, slug, deadline) if time.monotonic() < deadline else {"skipped": "no budget left"}
    wall = time.monotonic() - started
    after_self, after_children = _cpu()
    after = progress(net, slug)
    timing = _delta(compile_timing.snapshot(), timing_before)
    cpu = {"self": round(after_self - cpu_self, 3), "children": round(after_children - cpu_children, 3)}
    cpu["total"] = round(cpu["self"] + cpu["children"], 3)
    # A compiler run in the Linux VM (a PS2 game's EE-GCC on a Mac) or WSL spends its CPU outside this process's
    # children: os.times can't see it, so matches per CPU-minute would look far better than it is.
    cpu["complete"] = os.name != "nt" and not any(timing.get(f"driver_{mode}") for mode in ("linux-vm", "wsl"))
    new = after["matched"] - before["matched"]
    requests = timing.get("requests", 0)
    return {
        "console": console.id, "demo": console.demo, "compiler": (net.cfg(net.project_row(slug)).get("compiler") or {}).get("toolchain", ""),
        "import_seconds": round(import_seconds, 3), "budget_seconds": seconds, "wall_seconds": round(wall, 3),
        "cpu_seconds": cpu, "before": before, "after": after, "new_matches": new,
        "matches_per_cpu_minute": round(new / (cpu["total"] / 60), 3) if cpu["total"] > 0 else None,
        "matches_per_wall_minute": round(new / (wall / 60), 3) if wall > 0 else None,
        "cache_hit_rate": round(timing.get("cache_hits", 0) / requests, 4) if requests else None,
        "compile_timing": timing, "phases": phases,
    }


def run(wanted: list[str] | None = None, *, seconds: float = SECONDS, workers: int | None = None,
        permuter: bool = False, home: Path | None = None, log=print) -> dict:
    """Every chosen demo with its tools, one after another (not at once: they would share the CPU being measured).
    The caller has isolated the environment (`isolate`) before importing the app."""
    home = Path(home or os.environ["DECOMP_HOME"])
    report = {"format": FORMAT, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "machine": {"system": platform.system(), "machine": platform.machine(), "cpus": os.cpu_count(),
                          "python": platform.python_version()},
              "revision": _revision(), "settings": {"seconds": seconds, "workers": workers, "permuter": permuter},
              "demos": {}, "skipped": {}}
    for console, reason in demos(wanted):
        if reason:
            report["skipped"][console.id] = reason
            log(f"{console.id}: skipped, {reason}")
            continue
        log(f"{console.id}: building and importing {console.demo}, then {seconds:g} s of machine matching")
        try:
            result = bench_demo(console, home / console.id, seconds, workers=workers, permuter=permuter)
        except (OSError, subprocess.SubprocessError) as error:  # a demo that won't build or import: say why, go on
            report["skipped"][console.id] = f"failed: {error}"
            log(f"{console.id}: failed, {error}")
            continue
        report["demos"][console.id] = result
        log(f"{console.id}: {result['before']['matched']} -> {result['after']['matched']} of "
            f"{result['after']['functions']} matched, {result['cpu_seconds']['total']:.1f} CPU s, "
            f"{result['matches_per_cpu_minute']} matches per CPU-minute")
    return report


def _revision() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def compare(a: dict, b: dict) -> str:
    """A before/after table, one block per demo both runs measured, for a pull request's description."""
    if a.get("format") != FORMAT or b.get("format") != FORMAT:
        raise SystemExit(f"both files must be decompnet.bench output, format {FORMAT}")
    lines = [f"before: {a.get('revision') or '?'} ({a['settings']})", f"after:  {b.get('revision') or '?'} ({b['settings']})"]
    if a.get("machine") != b.get("machine"):
        lines.append("warning: measured on different machines; the CPU figures don't compare")

    def row(label: str, old, new) -> str:
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            change = new - old
            percent = f" ({change / old:+.1%})" if old else ""
            return f"  {label:<28} {old:>12.4g} {new:>12.4g} {change:>+12.4g}{percent}"
        return f"  {label:<28} {old!s:>12} {new!s:>12}"
    for demo in sorted(set(a["demos"]) | set(b["demos"])):
        old, new = a["demos"].get(demo), b["demos"].get(demo)
        if old is None or new is None:
            lines.append(f"{demo}: only in the {'after' if old is None else 'before'} run")
            continue
        lines.append(f"{demo} ({new['compiler']}){'' if old['compiler'] == new['compiler'] else ' compiler changed!'}")
        if not (old["cpu_seconds"].get("complete", True) and new["cpu_seconds"].get("complete", True)):
            lines.append("  note: some compiler CPU ran outside this process's children (VM, WSL or Windows): "
                         "compare the wall-minute figures")
        lines.append(f"  {'':<28} {'before':>12} {'after':>12} {'change':>12}")
        for label, key in (("matched after", lambda r: r["after"]["matched"]), ("new matches", lambda r: r["new_matches"]),
                           ("matches per CPU-minute", lambda r: r["matches_per_cpu_minute"]),
                           ("matches per wall-minute", lambda r: r["matches_per_wall_minute"]),
                           ("wall seconds", lambda r: r["wall_seconds"]), ("CPU seconds", lambda r: r["cpu_seconds"]["total"]),
                           ("cache hit rate", lambda r: r["cache_hit_rate"])):
            lines.append(row(label, key(old), key(new)))
        for name in TIMING_LINES:
            lines.append(row(f"timing: {name}", old["compile_timing"].get(name, 0), new["compile_timing"].get(name, 0)))
    for which, report in (("before", a), ("after", b)):
        for demo, reason in report.get("skipped", {}).items():
            lines.append(f"{demo}: skipped in the {which} run, {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m decompnet.bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo", action="append", help="a console id (n64, ps, ps2, gc, ...); repeat for several. "
                                                        "Default: every demo whose tools are installed")
    parser.add_argument("--seconds", type=float, default=SECONDS, help=f"wall-clock budget per demo (default {SECONDS})")
    parser.add_argument("--workers", type=int, help="autopilot's workers (default: what the app's button uses)")
    parser.add_argument("--permuter", action="store_true", help="after autopilot, permute the near misses left")
    parser.add_argument("--home", type=Path, help="the throwaway home to use (default: a new temporary folder, removed after)")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway home, to look at its projects")
    parser.add_argument("--out", type=Path, help="write the JSON here as well as to standard output")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"), help="compare two runs' JSON")
    args = parser.parse_args(argv)
    if args.compare:
        print(compare(*(json.loads(p.read_text()) for p in args.compare)))
        return 0
    if not 1 <= args.seconds <= 24 * 3600:
        parser.error("choose a budget of 1 second to a day")
    made = args.home is None
    home = Path(tempfile.mkdtemp(prefix="decomp-bench-")) if made else args.home
    isolate(home)
    try:
        report = run(args.demo, seconds=args.seconds, workers=args.workers, permuter=args.permuter, home=home,
                     log=lambda line: print(line, file=sys.stderr, flush=True))
    finally:
        if made and not args.keep:
            shutil.rmtree(home, ignore_errors=True)
        elif args.keep:
            print(f"kept the benchmark's home: {home}", file=sys.stderr)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    # daemon threads (twins, drafts) may still be winding down: leave without waiting on them
    code = main()
    sys.stdout.flush()
    os._exit(code)
