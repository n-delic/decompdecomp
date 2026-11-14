"""Does a function behave like the original? (docs/N64RECOMP.md, phase 1; the compare_behaviour tool)

Matching proves bytes, not behaviour in a port: C that matches can still do something else once another compiler
builds it (the backwards race start: a $v0 lost through a void function). This runs a function's real calls both
ways and compares what each did:

1. the user's ROM in the console's reversing emulator (Console.emulator) stops at the function's first calls and
   records each one's registers and memory;
2. each recorded call runs through the function's recompiled C (Console.recompiler: the game's own instructions as
   C, so it behaves as the original), and through the port's build of its decompiled C (port/wasm: the host's
   PORT_CALL, from the project's last save);
3. their results ($v0, $v1, $f0) and the memory each left are compared word by word, the stack below the call's $sp
   aside (where the recompiled C keeps its frames and the decompiled C keeps none).

What the recompiled C reaches of the runtime (libultra the recompiler leaves to it: stubs here) is said beside the
call, since a difference after one is not the function's own. Recordings and results go in the project's build
folder (derived from the ROM: git ignores it).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..network import IN_BUILD, NetworkError

WAIT = 40.0  # seconds a tool call waits, under the MCP gateway's deadline (docs/MCP_GATEWAY.md)
KEEP = 1800.0
STACK = 0x10000  # bytes below a call's $sp that are the callee's frames, not its effects
_runs: dict[tuple, dict] = {}
_lock = threading.Lock()


def later(key: tuple, work, wait: float | None = None) -> tuple[bool, object]:
    """Run `work()` once per key in the background; (True, its result) when it finished within `wait` seconds (or
    had already), else (False, seconds so far). A failure is raised to the call that collects it, and the next call
    with the same key starts again. Finished results are kept KEEP seconds."""
    now = time.monotonic()
    with _lock:
        for k in [k for k, r in _runs.items() if r.get("done") and now - r["done"] > KEEP]:
            del _runs[k]
        entry = _runs.get(key)
        if entry is None or entry.get("error") is not None and entry.get("collected"):
            entry = _runs[key] = {"started": now, "event": threading.Event()}

            def go(entry=entry):
                try:
                    entry["result"] = work()
                except Exception as error:  # (said to the caller; the next call starts again)
                    entry["error"] = error
                entry["done"] = time.monotonic()
                entry["event"].set()
            threading.Thread(target=go, name="decomp-later", daemon=True).start()
    entry["event"].wait(WAIT if wait is None else wait)
    if not entry["event"].is_set():
        return False, time.monotonic() - entry["started"]
    if entry.get("error") is not None:
        entry["collected"] = True
        raise entry["error"]
    return True, entry["result"]


def _state(path: Path) -> dict:
    words = path.read_text().split()
    return {"pc": int(words[0], 16), "gpr": [int(w, 16) for w in words[1:33]],
            "fpr": [int(w, 16) for w in words[35:67]]}


def _result(path: Path) -> tuple[int, ...]:
    return tuple(int(x, 16) for x in path.read_text().split()[:4])


def _port(net, slug: str, log) -> tuple[Path, Path]:
    """The port's program (built from the last save, only what changed) and the ROM it runs."""
    from ..port.wasm import build, play
    repo, cfg, rom = play.game(net, slug)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, leave_out=play.library(net, slug, log))
    return program, rom


def _port_call(program: Path, rom: Path, vram: int, state: Path, rdram: Path, out: Path, timeout=60) -> dict:
    env = {**os.environ, "PORT_CALL": f"{vram:08X}", "PORT_CALL_STATE": str(state), "PORT_CALL_RDRAM": str(rdram),
           "PORT_CALL_OUT": str(out)}
    try:
        done = subprocess.run([str(program), str(rom)], cwd=program.parent, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"didn't return within {timeout:.0f} s (a thread waiting on another?)"}
    result = Path(f"{out}.result")
    if done.returncode or not result.is_file():
        return {"ok": False, "error": (done.stderr.strip().splitlines() or [f"stopped ({done.returncode})"])[-1]}
    v0, v1, f0, f1 = _result(result)
    return {"ok": True, "v0": v0, "v1": v1, "f0": f0, "f1": f1, "rdram": Path(f"{out}.rdram")}


def compare_calls(before: bytes, recomp: dict, port: dict, sp: int, label) -> list[str]:
    """What differs between the recompiled and the decompiled call: results, then memory outside the stack."""
    from ..consoles.n64.emulator import differences
    out = []
    for key, name in (("v0", "$v0"), ("v1", "$v1"), ("f0", "$f0")):
        if recomp[key] != port[key]:
            out.append(f"{name}: recompiled {recomp[key]:08X}, decompiled {port[key]:08X}")
    a, b = recomp["rdram"].read_bytes(), port["rdram"].read_bytes()
    ranges = [r for r in differences(a, b, 0x80000000, 0x80000000 + min(len(a), len(b)))
              if not sp - STACK <= r[0] < sp]
    for start, end, words in ranges[:12]:
        mine = int.from_bytes(a[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        theirs = int.from_bytes(b[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        was = int.from_bytes(before[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        out.append(f"memory at {label(start)} ({words} words to {end:08X}): recompiled {mine:08X}, decompiled "
                   f"{theirs:08X}, before the call {was:08X}")
    if len(ranges) > 12:
        out.append(f"... {len(ranges) - 12} more ranges")
    return out


def compare(net, slug: str, function: str, calls: int = 3, vis: int = 3600, from_vi: int = 0, input: str = "",
            expansion_pak: bool = False, log=lambda *a: None) -> str:
    with net.progress.view():
        proj = net.project_row(slug)
        console = net.console(proj)
        row = net.project_store(proj["id"]).one(
            f"SELECT name, vram, status FROM functions WHERE project_id=? AND name=? AND {IN_BUILD}", proj["id"],
            function)
        symbols = net.symbols(proj["id"])
    if console.emulator is None or console.recompiler is None:
        raise NetworkError(f"{console.name} has no reversing emulator and static recompiler in decompdecomp yet: "
                           "compare_behaviour runs N64 games for now")
    if not net.is_rom(proj):
        raise NetworkError("compare_behaviour runs projects imported from a ROM")
    if row is None or row["vram"] is None:
        raise NetworkError(f"no function {function!r} in this game: give a function's name as list_functions shows it")
    vram = row["vram"]
    root = Path(proj["root"])
    rom = root / net.cfg(proj)["rom"]["file"]
    folder = root / "build" / "behaviour" / function
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    log(f"recording {function}'s first {calls} calls in the reversing emulator")
    run = console.emulator.run(rom, vis, inputs=input.strip(), breaks=[vram], report_from=from_vi,
                               expansion_pak=expansion_pak, record=(folder, calls), timeout=1500)
    if not run.recorded:
        return (f"{function} wasn't called in {'VIs ' + str(from_vi) + ' to ' if from_vi else 'the first '}{run.vis} VIs"
                " of the game: give input to reach where it runs, more VIs, or a later from_vi.")
    recomp_folder = console.recompiler.folder(root)
    if console.recompiler.load(root) is None:
        from .recompile import run as recompile
        recompile(net, slug, log)
    entry = int.from_bytes(rom.read_bytes()[8:12], "big")
    harness = console.recompiler.harness(recomp_folder, entry, log)
    program, port_rom = _port(net, slug, log)
    lines = [f"{function} ({row['status']}), {len(run.recorded)} calls recorded on the console"
             + (f" from VI {from_vi}" if from_vi else "") + ", each run through its recompiled C (the original's "
             "instructions) and its decompiled C as the port builds it:"]
    same = 0
    for i, base in enumerate(run.recorded):
        state, rdram = Path(f"{base}.state"), Path(f"{base}.rdram")
        regs = _state(state)
        sp = regs["gpr"][29] & 0xFFFFFFFF
        args = " ".join(f"{regs['gpr'][r] & 0xFFFFFFFF:08X}" for r in (4, 5, 6, 7))
        recomp = console.recompiler.run_one(harness, vram, state, rdram, Path(f"{base}.recomp"))
        port = _port_call(program, port_rom, vram, state, rdram, Path(f"{base}.port"))
        head = f"- call {i + 1} (a0-a3 {args})"
        if not recomp["ok"] or not port["ok"]:
            lines.append(f"{head}: " + "; ".join(f"{side} C: {r['error']}" for side, r in
                                                 (("recompiled", recomp), ("decompiled", port)) if not r["ok"]))
            continue
        found = compare_calls(rdram.read_bytes(), recomp, port, sp, symbols.label)
        reached = f" (the recompiled call reached the runtime's {', '.join(recomp['runtime'])}: stubs here)" \
            if recomp["runtime"] else ""
        if found:
            lines.append(f"{head}: differs{reached}")
            lines += [f"    {f}" for f in found]
        else:
            same += 1
            lines.append(f"{head}: the same, $v0 {port['v0']:08X}{reached}")
    lines.append(f"{same} of {len(run.recorded)} calls behave the same."
                 + ("" if same == len(run.recorded) else " A difference is the decompiled C doing something the "
                    "original doesn't, once built by another compiler: its declarations, types or what it returns."))
    return "\n".join(lines)


def answer(net, slug: str, function: str, calls: int = 3, vis: int = 3600, from_vi: int = 0, input: str = "",
           expansion_pak: bool = False) -> str:
    """The tool's answer: the comparison, or "still running" when it outlasts a call (the same call collects it)."""
    calls = max(1, min(int(calls), 8))
    vis, from_vi = int(vis), int(from_vi)
    if not 1 <= vis <= 60 * 60 * 5 or not 0 <= from_vi < vis:
        raise NetworkError("vis is 1 to 18000 (60 a second), and from_vi below it")
    key = ("behaviour", slug, function, calls, vis, from_vi, input.strip(), bool(expansion_pak))
    done, value = later(key, lambda: compare(net, slug, function, calls, vis, from_vi, input, expansion_pak))
    if not done:
        return (f"Still running ({value:.0f} s so far: recording the calls, then building the recompiled and decompiled "
                "C the first time). Call compare_behaviour again with the same arguments for the result.")
    return value
