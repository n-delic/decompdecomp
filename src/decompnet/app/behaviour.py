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

import concurrent.futures
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
    words = path.read_text().split()
    return (*(int(x, 16) for x in words[:4]), int(words[4]) if len(words) > 4 else 1)


RETURNS = ("nothing", "a word", "64 bits", "a float", "a double")


def v0_takers(program: Path) -> set[str]:
    """Functions declared void whose $v0 a caller takes (the build's calls.json, port/wasm/calls.py): their $v0 is a
    result; any other void function's is whatever its last instruction left, and not compared."""
    import json
    try:
        found = json.loads((Path(program).parent / "calls.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {r["callee"] for records in found.values() for r in records if r.get("kind") == "void-result"}


def _port(net, slug: str, log) -> tuple[Path, Path]:
    """The port's program (built from the last save, only what changed) and the ROM it runs."""
    from ..port.wasm import build, play
    repo, cfg, rom = play.game(net, slug)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, **play.inputs(net, slug, log))
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
    v0, v1, f0, f1, kind = _result(result)
    return {"ok": True, "v0": v0, "v1": v1, "f0": f0, "f1": f1, "kind": kind, "rdram": Path(f"{out}.rdram")}


def _return_address(memory: bytes, value: int) -> bool:
    """A value that is where a call returns to: the instruction two words before it is a jal or jalr."""
    if not 0x80000008 <= value < 0x80000000 + len(memory) or value & 3:
        return False
    o = (value - 8) & 0x1FFFFFFF
    word = int.from_bytes(memory[o:o + 4], "big")
    return word >> 26 == 3 or (word >> 26 == 0 and word & 0x3F == 9)


def compare_calls(before: bytes, recomp: dict, port: dict, sp: int, label, v0_taken: bool = True,
                  notes: list | None = None) -> list[str]:
    """What differs between the recompiled and the decompiled call: what the function returns (the registers that
    hold it: $v0 for a word, $v0 and $v1 for 64 bits, $f0 ($f1) for a float (double); a void function's $v0 only
    when a caller takes it), then memory outside the stack. A word where the recompiled C stored a return address
    and the decompiled C 0 goes to `notes` instead: C has no $ra (a game's heap that records who called it), so a
    port can't store it, and nothing else about the call differs for it."""
    from ..consoles.n64.emulator import differences
    out = []
    kind = port.get("kind", 1)
    registers = {0: ("v0",) if v0_taken else (), 1: ("v0",), 2: ("v0", "v1"), 3: ("f0",), 4: ("f0", "f1")}[kind]
    for key, name in (("v0", "$v0"), ("v1", "$v1"), ("f0", "$f0"), ("f1", "$f1")):
        if key in registers and recomp[key] != port[key]:
            out.append(f"{name}: recompiled {recomp[key]:08X}, decompiled {port[key]:08X}")
    a, b = recomp["rdram"].read_bytes(), port["rdram"].read_bytes()
    ranges = [r for r in differences(a, b, 0x80000000, 0x80000000 + min(len(a), len(b)))
              if not sp - STACK <= r[0] < sp]
    real = []
    for start, end, words in ranges:
        mine = int.from_bytes(a[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        theirs = int.from_bytes(b[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        if words == 1 and theirs == 0 and _return_address(before, mine):
            if notes is not None:
                notes.append(f"{label(start)}: the recompiled C stored the return address {mine:08X}, which C "
                             "doesn't have")
            continue
        real.append((start, end, words))
    ranges = real
    for start, end, words in ranges[:12]:
        mine = int.from_bytes(a[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        theirs = int.from_bytes(b[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        was = int.from_bytes(before[start & 0x1FFFFFFF:(start & 0x1FFFFFFF) + 4], "big")
        out.append(f"memory at {label(start)} ({words} words to {end:08X}): recompiled {mine:08X}, decompiled "
                   f"{theirs:08X}, before the call {was:08X}")
    if len(ranges) > 12:
        out.append(f"... {len(ranges) - 12} more ranges")
    return out


def _project(net, slug: str):
    with net.progress.view():
        proj = net.project_row(slug)
        console = net.console(proj)
        symbols = net.symbols(proj["id"])
    if console.emulator is None or console.recompiler is None:
        raise NetworkError(f"{console.name} has no reversing emulator and static recompiler in decompdecomp yet: "
                           "behaviour is checked on N64 games for now")
    if not net.is_rom(proj):
        raise NetworkError("Behaviour is checked on projects imported from a ROM")
    return proj, console, symbols


def _sides(net, slug: str, proj, console, log, stop=lambda: False):
    """The recompiled C's harness (Recompile first, when it never ran) and the port's program, with its ROM."""
    root = Path(proj["root"])
    rom = root / net.cfg(proj)["rom"]["file"]
    from . import recompile
    entry = int.from_bytes(rom.read_bytes()[8:12], "big")
    with recompile.ready(net, slug, proj, console.recompiler, log, stop) as folder:
        harness = console.recompiler.harness(folder, entry, log, stop)
    program, port_rom = _port(net, slug, log)
    return rom, harness, program, port_rom


def one_call(console, harness, program, port_rom, vram: int, base: Path, label,
             v0_taken: bool = True) -> tuple[str, list[str], dict]:
    """One recorded call (base.state, base.rdram) both ways: ("same" | "differs" | "error", what differs or why,
    {"v0", "runtime", "args"})."""
    state, rdram = Path(f"{base}.state"), Path(f"{base}.rdram")
    regs = _state(state)
    sp = regs["gpr"][29] & 0xFFFFFFFF
    info = {"args": [regs["gpr"][r] & 0xFFFFFFFF for r in (4, 5, 6, 7)], "runtime": []}
    recomp = console.recompiler.run_one(harness, vram, state, rdram, Path(f"{base}.recomp"))
    port = _port_call(program, port_rom, vram, state, rdram, Path(f"{base}.port"))
    if not recomp["ok"] or not port["ok"]:
        return "error", [f"{side} C: {r['error']}" for side, r in (("recompiled", recomp), ("decompiled", port))
                         if not r["ok"]], info
    info.update(v0=port["v0"], runtime=recomp["runtime"])
    notes: list[str] = []
    found = compare_calls(rdram.read_bytes(), recomp, port, sp, label, v0_taken, notes)
    info["notes"] = notes
    return ("differs" if found else "same"), found, info


def compare(net, slug: str, function: str, calls: int = 3, vis: int = 3600, from_vi: int = 0, input: str = "",
            expansion_pak: bool = False, log=lambda *a: None) -> str:
    proj, console, symbols = _project(net, slug)
    with net.progress.view():
        row = net.project_store(proj["id"]).one(
            f"SELECT name, vram, status FROM functions WHERE project_id=? AND name=? AND {IN_BUILD}", proj["id"],
            function)
    if row is None or row["vram"] is None:
        raise NetworkError(f"no function {function!r} in this game: give a function's name as list_functions shows it")
    vram = row["vram"]
    root = Path(proj["root"])
    folder = root / "build" / "behaviour" / function
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    log(f"recording {function}'s first {calls} calls in the reversing emulator")
    rom = root / net.cfg(proj)["rom"]["file"]
    run = console.emulator.run(rom, vis, inputs=input.strip(), breaks=[vram], report_from=from_vi,
                               expansion_pak=expansion_pak, record=(folder, calls), timeout=1500)
    if not run.recorded:
        return (f"{function} wasn't called in {'VIs ' + str(from_vi) + ' to ' if from_vi else 'the first '}{run.vis} VIs"
                " of the game: give input to reach where it runs, more VIs, or a later from_vi.")
    _, harness, program, port_rom = _sides(net, slug, proj, console, log)
    taken = function in v0_takers(program)
    lines = [f"{function} ({row['status']}), {len(run.recorded)} calls recorded on the console"
             + (f" from VI {from_vi}" if from_vi else "") + ", each run through its recompiled C (the original's "
             "instructions) and its decompiled C as the port builds it:"]
    same = 0
    for i, base in enumerate(run.recorded):
        verdict, found, info = one_call(console, harness, program, port_rom, vram, base, symbols.label, taken)
        head = f"- call {i + 1} (a0-a3 {' '.join(f'{a:08X}' for a in info['args'])})"
        reached = f" (the recompiled call reached the runtime's {', '.join(info['runtime'])}: stubs here)" \
            if info["runtime"] else ""
        if verdict == "error":
            lines.append(f"{head}: " + "; ".join(found))
        elif verdict == "differs":
            lines.append(f"{head}: differs{reached}")
            lines += [f"    {f}" for f in found]
        else:
            same += 1
            lines.append(f"{head}: the same, $v0 {info['v0']:08X}{reached}")
        lines += [f"    note: {n}" for n in info.get("notes", [])]
    lines.append(f"{same} of {len(run.recorded)} calls behave the same."
                 + ("" if same == len(run.recorded) else " A difference is the decompiled C doing something the "
                    "original doesn't, once built by another compiler: its declarations, types or what it returns."))
    return "\n".join(lines)


CHUNK = 4096  # functions recorded per emulator run at most (its MAX_BREAKS may say fewer)
WORKERS = max(1, min(8, (os.cpu_count() or 2) - 1))  # calls compared at once


def results_file(root: Path) -> Path:
    return Path(root) / "build" / "behaviour" / "batch.json"


def batch(net, slug: str, vis: int = 3600, input: str = "", expansion_pak: bool = False, log=print) -> dict:
    """Check behaviour (Porting): every matched function's first call in the first `vis` VIs, recorded in chunks,
    each run both ways. Results are saved after every chunk (results_file), each with its function's matched C: a
    run stopped or started again checks only what is new or changed; other settings check everything again."""
    import hashlib
    import json
    proj, console, symbols = _project(net, slug)
    stop = getattr(log, "cancel_event", None) or threading.Event()
    report = getattr(log, "progress", None) or (lambda value: None)
    root = Path(proj["root"])
    rom = root / net.cfg(proj)["rom"]["file"]
    settings = {"vis": int(vis), "input": input.strip(), "expansion_pak": bool(expansion_pak)}
    with net.progress.view():
        rows = net.project_store(proj["id"]).all(
            f"SELECT name, vram, matched_source FROM functions WHERE project_id=? AND status='matched' AND {IN_BUILD} "
            "AND vram IS NOT NULL ORDER BY vram", proj["id"])
    path = results_file(root)
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        saved = {}
    results = saved.get("functions", {}) if saved.get("settings") == settings else {}
    source = {r["name"]: hashlib.sha256((r["matched_source"] or "").encode()).hexdigest()[:16] for r in rows}
    todo = [r for r in rows if results.get(r["name"], {}).get("source") != source[r["name"]]]
    results = {n: v for n, v in results.items() if n in source}

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"settings": settings, "at": time.time(), "functions": results}, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)

    log(f"{len(rows)} matched functions; {len(rows) - len(todo)} checked already, {len(todo)} to check")
    if not todo:
        save()
        return summary(root)
    report({"phase": "Building the recompiled and decompiled C", "step": 1, "steps": 2})
    _, harness, program, port_rom = _sides(net, slug, proj, console, log, stop.is_set)
    takers = v0_takers(program)
    lock = threading.Lock()
    work = root / "build" / "behaviour" / "batch"
    chunk_size = min(CHUNK, getattr(console.emulator, "MAX_BREAKS", CHUNK))
    done = 0
    stopped = "Stopped: the functions checked so far are saved; Check behaviour goes on from there."
    for at in range(0, len(todo), chunk_size):
        if stop.is_set():
            raise NetworkError(stopped)
        chunk = todo[at:at + chunk_size]
        by_vram = {r["vram"]: r for r in chunk}
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        log(f"running the game to record the first call of {len(chunk)} functions"
            + (f" ({at + 1}-{at + len(chunk)} of {len(todo)})" if len(chunk) < len(todo) else ""))

        arrived: set[int] = set()

        def compare_one(base: Path, by_vram=by_vram, arrived=arrived) -> None:
            nonlocal done
            try:
                vram = int(base.name.split("_")[0], 16)
                with lock:
                    arrived.add(vram)
                r = by_vram.get(vram)
                if r is not None and not stop.is_set():
                    verdict, found, info = one_call(console, harness, program, port_rom, r["vram"], base,
                                                    symbols.label, r["name"] in takers)
                    with lock:
                        results[r["name"]] = {"source": source[r["name"]], "vram": r["vram"], "verdict": verdict,
                                              "found": found[:6], "runtime": info["runtime"],
                                              "notes": info.get("notes", [])[:3]}
                        done += 1
                        report({"phase": "Running the game and comparing each function's first call", "step": 2,
                                "steps": 2, "done": done, "total": len(todo)})
                        if done % 32 == 0:
                            save()
            finally:
                for f in base.parent.glob(base.name + ".*"):
                    f.unlink(missing_ok=True)  # 4 MB each: only the verdict is kept

        # each recording compared as it comes, several at once (most of a call is starting the port's program);
        # the emulation waits while the pool has its fill of recordings (EMU_RECORD_PENDING, three files each)
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            try:
                console.emulator.run(rom, vis, inputs=input.strip(), breaks=list(by_vram), expansion_pak=expansion_pak,
                                     record=(work, 1), timeout=3600, stop=stop.is_set, pending=3 * WORKERS,
                                     on_record=lambda base: pool.submit(compare_one, base))
            except Exception:
                pool.shutdown(wait=True, cancel_futures=True)
                save()
                if stop.is_set():
                    raise NetworkError(stopped) from None
                raise
        if stop.is_set():  # recorded but not compared yet: still to do next time, not "not reached"
            save()
            raise NetworkError(stopped)
        for r in chunk:
            if r["vram"] not in arrived:
                results[r["name"]] = {"source": source[r["name"]], "vram": r["vram"], "verdict": "not reached"}
        save()
        counts = summary(root)
        log(f"{counts['same']} the same, {counts['differs']} differ, {counts['not_reached']} not reached, "
            f"{counts['errors']} couldn't run")
    shutil.rmtree(work, ignore_errors=True)
    return summary(root)


def summary(root: Path) -> dict | None:
    """The last Check behaviour's results, counted (Port readiness): None before one ran."""
    import json
    path = results_file(root)
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None
    if not saved:
        return None
    fns = saved.get("functions", {})
    count = lambda v: sum(1 for e in fns.values() if e.get("verdict") == v)  # noqa: E731
    differs = sorted((n for n, e in fns.items() if e.get("verdict") == "differs"), key=lambda n: fns[n].get("vram", 0))
    return {"at": saved.get("at"), "settings": saved.get("settings"), "checked": len(fns), "same": count("same"),
            "return_addresses": sum(1 for e in fns.values() if e.get("verdict") == "same" and e.get("notes")),
            "differs": len(differs), "not_reached": count("not reached"), "errors": count("error"),
            "examples": [f"{n}: {(fns[n].get('found') or ['differs'])[0]}" for n in differs[:8]],
            "functions_differing": differs}


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
