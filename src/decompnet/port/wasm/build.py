"""Build a finished game as WebAssembly, translate it back to C and build it with the console's host (port/wasm).

Inputs, all from the game's save repository (the folder Save writes, under git) and the console's port kit:
- progress/src/*.c and progress/include/*.h: the game's matched C and its shared header, as the decomp has them;
- progress/decompdecomp.json: where every function and named datum lives (the layout and the symbols);
- port/wasm/exclude.txt: matched C the build leaves out, one file name a line (# comments): library code the host
  provides, or code the port doesn't run;
- port/wasm/src/*.c: C only the port has, for functions with no matched C yet (it needn't match, only behave);
- port/wasm/overrides/*.c: the port's version of a matched file, until the fix is in the matched C behind
  `#ifdef PORT` (Fix C for the PC);
- port/wasm/include/*.h: headers only the port's C includes.

The build goes to port/wasm/build/ (generated, never committed: git ignores it). Its result is a program that runs
the game from the user's own ROM: `port/wasm/build/game ROM [seconds]`.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ... import consoles, limits, toolchains
from ...network import NetworkError

HERE = Path(__file__).parent
# The game's C as wasm32 IR, unoptimised: LLVM's load combining would assume little-endian memory, which beir.py and
# bemem.py make big-endian after it.
IR_FLAGS = ["-target", "wasm32-freestanding", "-std=gnu17", "-DPORT=1", "-DPORT_WASM=1", "-O0", "-g0",
            "-fno-sanitize=all", "-ffreestanding", "-fno-strict-aliasing", "-fwrapv", "-Wno-everything"]
# The console's memory (4 GB, so every console address is in it), its data placed by the C's own addresses, a
# stack per thread below it (the host's), and every function exported: the host finds them by address.
LINK_FLAGS = ["--no-entry", "--allow-undefined", "--export-all", "--export-table", "--growable-table",
              "--emit-relocs", "--global-base=1048576", "-z", "stack-size=65536", "--stack-first",
              "--initial-memory=4294967296", "--max-memory=4294967296"]
TIMEOUT = 900


def _write(path: Path, text: str) -> None:
    """Write only what changed, so a rebuild recompiles only the C that changed."""
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def _names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()} - {""}


def gather(repo: Path, out: Path) -> dict[str, Path]:
    """The C the build compiles, in out/src (one file per function, the shared header included) and out/include.
    Returns {file name: where it came from}."""
    progress, port = repo / "progress", repo / "port" / "wasm"
    if not (progress / "src").is_dir() or not (progress / "decompdecomp.json").is_file():
        raise NetworkError(f"{repo} has no progress/src and progress/decompdecomp.json: choose the game's save "
                           "repository (the folder Save writes)")
    exclude = _names(port / "exclude.txt")
    chosen = {p.name: p for p in sorted((progress / "src").glob("*.c")) if p.name not in exclude}
    for folder in (port / "src", port / "overrides"):
        chosen.update({p.name: p for p in sorted(folder.glob("*.c"))})
    (out / "src").mkdir(parents=True, exist_ok=True)
    (out / "include").mkdir(parents=True, exist_ok=True)
    # The decomp compiles every function with the shared header, or with its part's own when the save names one
    # (a function from the game's own source files: common_<part>.h, the manifest's function_headers).
    manifest = json.loads((progress / "decompdecomp.json").read_text(encoding="utf-8"))
    units = manifest.get("function_headers") or {}
    for name, source in chosen.items():
        text = source.read_text(encoding="utf-8")
        unit = units.get(name[:-2])
        header = f"common_{unit}.h" if unit else "common.h"
        if not re.search(r'^#include "common(_\w+)?\.h"', text, re.M):
            text = f'#include "{header}"\n' + text
        _write(out / "src" / name, text)
    for stale in (out / "src").glob("*.c"):
        if stale.name not in chosen:
            stale.unlink()
    for folder in (progress / "include", port / "include"):
        for header in folder.glob("*.h"):
            _write(out / "include" / header.name, header.read_text(encoding="utf-8"))
    return chosen


def addresses(manifest: dict) -> tuple[list[str], set[str]]:
    """fixed.txt's rows (name, address, kind) and the functions' names, from the save's manifest."""
    rows, functions = {}, set()
    for f in manifest.get("layout") or []:
        rows[f["name"]] = (f["vram"], "func")
        functions.add(f["name"])
    for s in manifest.get("symbols") or []:
        func = s.get("kind") == "func"
        rows.setdefault(s["name"], (s["address"], "func" if func else s.get("kind", "data")))
        if func:
            functions.add(s["name"])
    return [f"{n} 0x{a:08X} {k}" for n, (a, k) in sorted(rows.items(), key=lambda r: (r[1][0], r[0]))], functions


def host_compiler(zig: Path) -> list:
    """The C compiler that builds the host, the one native program here. The pinned Zig on Linux and Windows. On
    macOS the system's own clang (Xcode's Command Line Tools), like wine an exception to "never from PATH": Zig
    0.14.1 can't link against a current macOS's system library (macOS 27 left `_strlen` and the rest undefined), and
    the host has to. The game's own C is compiled by the pinned Zig everywhere."""
    if sys.platform == "darwin":
        if shutil.which("xcrun") is None:
            raise NetworkError("building the host needs Xcode's Command Line Tools on macOS: install them with "
                               "`xcode-select --install`, then build again")
        return ["xcrun", "clang"]
    return [zig, "cc"]


def _run(argv: list, log, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(limits.capped([str(a) for a in argv]), cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT)


def _parallel(jobs, work, log, what: str) -> list[str]:
    """Run work(job) for every job, a core each; the jobs that failed, with their first error line."""
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for job, error in zip(jobs, pool.map(work, jobs)):
            if error:
                failed.append(f"{job}: {error}")
    if failed:
        log(f"{len(failed)} {what} failed: " + "; ".join(failed[:5]))
    return failed


def renames(lls: list[Path], fixed: list[str]) -> dict[str, str]:
    """{name a call uses: the defined function at its address}: C still calling a function by an older name (its
    automatic one, before polishing renamed it) links in the decomp through the symbol table; here it must name the
    function wasm-ld has. Only a name that resolves to exactly one defined function is renamed."""
    defined, declared = set(), set()
    for ll in lls:
        for m in re.finditer(r"^(define|declare) [^@\n]*@([\w.$]+)\(", ll.read_text(encoding="utf-8"), re.M):
            (defined if m.group(1) == "define" else declared).add(m.group(2))
    where = {}
    for row in fixed:
        n, a, k = row.split()
        if k == "func":
            where[n] = int(a, 16)
    at = {}
    for n in defined:
        address = where.get(n)
        if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", n)):
            address = int(auto.group(1), 16)
        if address is not None:
            at.setdefault(address, set()).add(n)
    out = {}
    for n in declared - defined:
        address = where.get(n)
        if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", n)):
            address = int(auto.group(1), 16)
        if address is not None and len(at.get(address, ())) == 1:
            out[n] = next(iter(at[address]))
    return out


def function_table(header: str, fixed: list[str]) -> str:
    """funcs.c: the game's functions by console address, sorted, for calls through pointers (the host's port_func)."""
    where = {}
    for row in fixed:
        n, a, k = row.split()
        if k == "func":
            where[n] = int(a, 16)
    entries = []
    for m in re.finditer(r"/\* export: '([^']+)' \*/\n[^\n]*?(w2c_game_\w+)\(w2c_game\*", header):
        name = m.group(1)
        address = where.get(name)
        if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", name)):
            address = int(auto.group(1), 16)
        if address is not None:
            entries.append((address, m.group(2), name))
    entries.sort()
    lines = ['#include "game.h"', "/* The game's functions by their console address (generated). */",
             "typedef struct { unsigned address; void *func; } PortFunc;", "const PortFunc port_funcs[] = {"]
    lines += [f"    {{0x{a:08X}u, (void *){c}}}, /* {n} */" for a, c, n in entries]
    lines += ["};", f"const unsigned port_func_count = {len(entries)};"]
    return "\n".join(lines) + "\n"


def build(repo: Path, *, console: str = "n64", log=print) -> Path:
    """Build the game in the save repository `repo` for the PC; returns the program (port/wasm/build/game)."""
    repo = Path(repo).expanduser().resolve()
    kit = consoles.get(console).port_kit
    if kit is None or kit.wasm is None:
        raise NetworkError(f"{consoles.get(console).name} has no WebAssembly build yet (its port kit has no host)")
    host = Path(kit.wasm)
    out = repo / "port" / "wasm" / "build"
    for name in ("ll", "o", "c", "obj"):
        (out / name).mkdir(parents=True, exist_ok=True)
    _write(repo / "port" / "wasm" / ".gitignore", "build/\n")
    toolchains.ensure("zig", log=log)
    zig = toolchains.binary("zig")
    wabt = toolchains.ensure("wabt", log=log)
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(out / "zig-cache"), "ZIG_LOCAL_CACHE_DIR": str(out / "zig-cache")}
    py = sys.executable

    log("gathering the game's C from its save")
    sources = gather(repo, out)
    manifest = json.loads((repo / "progress" / "decompdecomp.json").read_text(encoding="utf-8"))
    fixed, functions = addresses(manifest)
    _write(out / "fixed.txt", "\n".join(fixed) + "\n")
    _write(out / "functions.txt", "\n".join(sorted(functions)) + "\n")

    log(f"compiling {len(sources)} files to WebAssembly IR")

    def ir(name: str) -> str | None:
        src, ll = out / "src" / name, out / "ll" / (name[:-2] + ".ll")
        if ll.exists() and ll.stat().st_mtime >= src.stat().st_mtime:
            return None
        done = _run([zig, "cc", *IR_FLAGS, "-I", out / "include", "-S", "-emit-llvm", src, "-o", ll], log, env=env)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["failed"])[0]
    if failed := _parallel(sorted(sources), ir, log, "files"):
        raise NetworkError(f"{len(failed)} of the game's files don't compile for the WebAssembly build: "
                           + "; ".join(failed[:3]))
    for stale in (out / "ll").glob("*.ll"):
        if stale.name[:-3] + ".c" not in sources:
            stale.unlink()

    log("one signature per function")
    lls = sorted((out / "ll").glob("*.ll"))
    done = _run([py, HERE / "sigs.py", out / "sigs.txt", host / "libultra.sigs", *lls], log)
    if done.returncode:
        raise NetworkError(f"reading the functions' signatures failed: {done.stderr.strip()[-400:]}")

    _write(out / "renames.txt", "".join(f"{a} {b}\n" for a, b in sorted(renames(lls, fixed).items())))
    log("placing every symbol at its console address, data big-endian")
    sigs_changed = max((out / n).stat().st_mtime for n in ("sigs.txt", "fixed.txt", "functions.txt", "renames.txt"))

    def place(ll: Path) -> str | None:
        be, obj = out / "o" / (ll.stem + ".be.ll"), out / "o" / (ll.stem + ".o")
        if obj.exists() and obj.stat().st_mtime >= max(ll.stat().st_mtime, sigs_changed):
            return None
        done = _run([py, HERE / "beir.py", ll, be, "--functions", out / "functions.txt", "--renames", out / "renames.txt",
                     "--signatures", out / "sigs.txt", "--fixed", out / "fixed.txt"], log)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["beir failed"])[-1]
        done = _run([zig, "cc", "-target", "wasm32-freestanding", "-O0", "-c", be, "-o", obj], log, env=env)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["failed"])[0]
    if failed := _parallel(lls, place, log, "functions"):
        raise NetworkError(f"{len(failed)} functions couldn't be placed at their addresses: " + "; ".join(failed[:3]))
    objects = sorted(p for p in (out / "o").glob("*.o") if p.stem + ".c" in sources)
    _write(out / "objects.txt", "\n".join(str(p) for p in objects) + "\n")

    log(f"linking {len(objects)} functions")
    done = _run([zig, "wasm-ld", "-o", out / "game.wasm", f"@{out / 'objects.txt'}", *LINK_FLAGS], log, env=env)
    if done.returncode:
        raise NetworkError(f"linking the WebAssembly build failed: {done.stderr.strip()[-600:]}")
    _run([py, HERE / "beslots.py", out / "game.wasm"], log)

    log("translating it to C (wasm2c)")
    shutil.rmtree(out / "c")
    (out / "c").mkdir()
    done = _run([wabt / "bin" / "wasm2c", out / "game.wasm", "-o", out / "c" / "game.c", "-n", "game",
                 "--num-outputs", "8"], log)
    if done.returncode:
        raise NetworkError(f"wasm2c failed: {done.stderr.strip()[-600:]}")
    done = _run([py, HERE / "bemem.py", out / "c" / "game-impl.h"], log)
    if done.returncode:
        raise NetworkError(f"making memory big-endian failed: {done.stderr.strip()[-400:]}")
    _write(out / "funcs.c", function_table((out / "c" / "game.h").read_text(encoding="utf-8"), fixed))
    _run([py, HERE / "stubs.py", out / "c" / "game.h", host / "host.c", out / "stubs.c"], log)

    log("building the host")
    rt = wabt / "share" / "wabt" / "wasm2c"
    units = [*sorted((out / "c").glob("game_*.c")), rt / "wasm-rt-impl.c", rt / "wasm-rt-mem-impl.c",
             rt / "wasm-rt-exceptions-impl.c", host / "host.c", out / "funcs.c", out / "stubs.c"]
    includes = ["-I", wabt / "include", "-I", rt, "-I", out / "c"]
    cc = host_compiler(zig)

    def compile_unit(unit: Path) -> str | None:
        obj = out / "obj" / (unit.stem + ".o")
        if obj.exists() and obj.stat().st_mtime >= unit.stat().st_mtime and unit.parent == out / "c":
            return None
        done = _run([*cc, "-O1", "-g", "-fno-inline", *includes, "-c", unit, "-o", obj], log, env=env)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["failed"])[0]
    if failed := _parallel(units, compile_unit, log, "host files"):
        raise NetworkError("building the host failed: " + "; ".join(failed[:3]))
    program = out / "game"
    done = _run([*cc, "-o", program, *(out / "obj" / (u.stem + ".o") for u in units), "-lpthread"], log, env=env)
    if done.returncode:
        raise NetworkError(f"linking the host failed: {done.stderr.strip()[-600:]}")
    log(f"built {program}: run it with the game's ROM, `{program} ROM [seconds]`")
    return program
