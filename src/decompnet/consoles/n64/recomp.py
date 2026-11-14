"""N64Recomp: the game's own instructions as C, for a port before the decomp is done and a reference for every
function (docs/N64RECOMP.md, phase 0). The N64's `Console.recompiler` part.

N64Recomp (MIT, pinned in toolchains.toml and built here from source like Torch) reads a symbols file (each
section's ROM offset, address and size, and its functions) and the user's ROM, and writes one C function per game
function that does exactly what its instructions do. decompdecomp writes that symbols file from the project's split
(`symbols`), into the project's build/recomp folder (derived from the ROM: kept out of git like extract/):

- code the split never listed as functions but other functions call (a `jal` into code splat left as data, a game's
  low boot code): each call target becomes a function, up to the next known start, and its own calls are followed in
  turn (`discover`). The summary lists them: the split has functions to find there;
- a function N64Recomp can't translate (it stops at the first) is set aside in its `ignored` list and it runs again,
  until every other function is C (`recompile`). The summary lists each with N64Recomp's reason.

The summary (build/recomp/recomp.json) is what Port readiness shows. Compiling the output needs the runtime's
macros (MEM_W, LOOKUP_FUNC...), the port's next step (docs/N64RECOMP.md, phase 2).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ... import limits

TOOL = "n64recomp"
JR_RA = 0x03E00008
MAX_RUNS = 400  # N64Recomp runs, each setting aside one more function it refuses


class RecompError(RuntimeError):
    pass


def build(dest: Path, cmake: Path, log=print) -> None:
    """Build N64Recomp's command line from the pinned sources unpacked in dest/source (toolchains.install)."""
    mac = sys.platform == "darwin"
    hint = ("Xcode Command Line Tools (`xcode-select --install`)" if mac else
            "a C++20 compiler and make (on Debian or Ubuntu: `sudo apt install g++ make`)")
    if not shutil.which("make") or not shutil.which("c++"):
        raise RecompError(f"N64Recomp is built from its source on this computer, which needs {hint}.")
    log("  building N64Recomp from its pinned source (first install only, under a minute)")
    with tempfile.TemporaryDirectory(prefix="decomp-n64recomp-") as tmp:
        steps = ([cmake, "-S", dest / "source", "-B", Path(tmp), "-G", "Unix Makefiles", "-DCMAKE_BUILD_TYPE=Release"],
                 [cmake, "--build", Path(tmp), "--parallel", str(min(8, os.cpu_count() or 1)), "--target",
                  "N64RecompCLI"])
        for argv in steps:
            try:
                done = subprocess.run(limits.capped([str(a) for a in argv]), cwd=tmp, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=1800)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RecompError(f"Building N64Recomp failed: {error}. It needs {hint}.") from error
            if done.returncode:
                raise RecompError(f"Building N64Recomp failed. It needs {hint}.\n" + (done.stdout + done.stderr)[-3000:])
        built = Path(tmp) / "N64Recomp"
        if not built.is_file():
            raise RecompError(f"N64Recomp's build finished without its program in {tmp}. It needs {hint}.")
        shutil.copyfile(built, dest / "N64Recomp")
        os.chmod(dest / "N64Recomp", 0o755)
        (dest / "include").mkdir(exist_ok=True)
        shutil.copyfile(dest / "source" / "include" / "recomp.h", dest / "include" / "recomp.h")
    shutil.rmtree(dest / "source", ignore_errors=True)


def _word(rom: bytes, offset: int) -> int:
    return int.from_bytes(rom[offset:offset + 4], "big") if 0 <= offset <= len(rom) - 4 else 0


def _segment(segments, vram: int):
    for s in segments:
        if s["vram"] <= vram < s["vram"] + s["rom_end"] - s["rom_start"]:
            return s
    return None


def discover(rom: bytes, segments: list[dict], functions: list[dict], entry: int | None = None) -> list[dict]:
    """Functions the split doesn't list but the code calls (and the entry point, when nothing starts there). A call
    into the body of a function the split lists is a label there, never a new function; a call into code only guessed
    at splits the guess. Each guess runs to the next start, ended after its last `jr $ra` before it, and its own calls
    are followed in turn (calls only: a guess may be data or RSP microcode, whose jumps lead nowhere real), until no
    call leads anywhere new. Each {"name", "vram", "rom", "size", "why"}."""
    import bisect
    spans = sorted((f["vram"], f["vram"] + f["size"]) for f in functions)
    known = {f["vram"] for f in functions}

    def in_known(vram: int) -> bool:
        i = bisect.bisect_right(spans, (vram, float("inf"))) - 1
        return i >= 0 and spans[i][0] < vram < spans[i][1]

    def calls(vram: int, rom_at: int, size: int, tail: bool):
        for i in range(0, size, 4):
            word = _word(rom, rom_at + i)
            if word >> 26 == 3 or (tail and word >> 26 == 2):  # jal; j out of the function (a tail call)
                target = ((vram + i + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
                if not vram <= target < vram + size:
                    yield target

    why: dict[int, str] = {}

    def want(target: int, reason: str) -> bool:
        if target in known or target in why or target & 3 or _segment(segments, target) is None or in_known(target):
            return False
        why[target] = reason
        return True

    if entry is not None:
        want(entry, "the ROM's entry point")
    for f in functions:
        for target in calls(f["vram"], f["rom"], f["size"], True):
            want(target, f"called from {f['name']}")

    def extent(vram: int) -> tuple[int, int]:
        s = _segment(segments, vram)
        end = s["vram"] + s["rom_end"] - s["rom_start"]
        later = [a for a in (*known, *why) if vram < a <= end]
        limit = min(later or [end])
        rom_at = s["rom_start"] + vram - s["vram"]
        last = None
        for a in range(vram, limit - 4, 4):
            if _word(rom, rom_at + a - vram) == JR_RA:
                last = a + 8  # with its delay slot
        return rom_at, (last or limit) - vram

    while True:  # until the guesses call nothing new (each new start can split a guess, so size them again)
        new = False
        for vram in list(why):
            rom_at, size = extent(vram)
            for target in calls(vram, rom_at, size, False):
                new |= want(target, f"called from func_{vram:08X}")
        if not new:
            break
    out = []
    for vram in sorted(why):
        rom_at, size = extent(vram)
        if size > 0:
            out.append({"name": f"func_{vram:08X}", "vram": vram, "rom": rom_at, "size": size, "why": why[vram]})
    return out


def _toml_name(name: str) -> str:
    return json.dumps(name)  # TOML basic strings take JSON's escapes


def symbols(segments: list[dict], functions: list[dict]) -> str:
    """N64Recomp's symbols file: every section and the functions in it (by ROM offset)."""
    out = []
    for s in segments:
        inside = sorted((f for f in functions if s["rom_start"] <= f["rom"] < s["rom_end"]), key=lambda f: f["rom"])
        out.append(f"[[section]]\nname = {_toml_name(s['name'])}\nrom = 0x{s['rom_start']:X}\nvram = 0x{s['vram']:X}\n"
                   f"size = 0x{s['rom_end'] - s['rom_start']:X}\n\nfunctions = [\n"
                   + "".join(f"    {{ name = {_toml_name(f['name'])}, vram = 0x{f['vram']:X}, size = 0x{f['size']:X} }},\n"
                             for f in inside) + "]\n")
    return "\n".join(out)


def config(rom: Path, entry: int | None, stubs: list[str], renamed: list[str]) -> str:
    return ("[input]\n" + (f"entrypoint = 0x{entry:X}\n" if entry is not None else "")
            + f'symbols_file_path = "syms.toml"\nrom_file_path = {json.dumps(str(rom))}\noutput_func_path = "funcs"\n'
            + "\n[patches]\nstubs = [" + ", ".join(_toml_name(n) for n in stubs) + "]\n"
            + "renamed = [" + ", ".join(_toml_name(n) for n in renamed) + "]\n")


ERROR = re.compile(r"^Error recompiling (\S+)")
UNDECLARED = re.compile(r"undeclared function '(\w+)'")
CONFLICT = re.compile(r"(?:conflicting types for|redefinition of) '(\w+)'")


def _translate(program, out, rom, entry, refused, renamed, stop, log):
    """N64Recomp until it translates everything: each function it refuses (it stops at the first) becomes a stub,
    declared with an empty body, and it runs again. Adds to `refused` {name: N64Recomp's reason}."""
    for run in range(MAX_RUNS):
        if stop():
            raise RecompError("Stopped.")
        (out / "recomp.toml").write_text(config(rom, entry, list(refused), sorted(renamed)), encoding="utf-8")
        shutil.rmtree(out / "funcs", ignore_errors=True)
        (out / "funcs").mkdir(parents=True)
        done = subprocess.run(limits.capped([str(program), "recomp.toml"]), cwd=out, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
        text = done.stdout + done.stderr
        if done.returncode == 0:
            return
        failed = next((m.group(1) for line in text.splitlines() if (m := ERROR.match(line))), None)
        if failed is None or failed in refused:
            raise RecompError("N64Recomp stopped: " + (text.strip().splitlines() or ["no output"])[-1])
        reason = next((line for line in text.splitlines() if line.strip() and not ERROR.match(line)
                       and not line.startswith(("Error in recompiling", "Function count", "Working dir", "["))), "")
        refused[failed] = reason.strip()
        if run % 10 == 9:
            log(f"N64Recomp: {len(refused)} functions set aside so far")
    raise RecompError(f"N64Recomp refused more than {MAX_RUNS} functions: the split may be far off")


def check(out: Path, include: Path, stop=lambda: False) -> tuple[list[str], set[str], list[str]]:
    """The output checked with this computer's C compiler, as a port would build it (syntax only, nothing runs).
    Returns the functions the runtime has to provide (N64Recomp leaves out the libultra ones its runtime
    reimplements: declared in funcs/runtime.h), names that clash with the C library, and what else is wrong."""
    files = sorted((out / "funcs").glob("*.c"))
    runtime = out / "funcs" / "runtime.h"
    provided: set[str] = set()
    clashes: set[str] = set()
    errors: list[str] = []
    for second in (False, True):
        runtime.write_text("#include \"recomp.h\"\n" + "".join(
            f"RECOMP_FUNC void {n}(uint8_t* rdram, recomp_context* ctx);\n" for n in sorted(provided)), encoding="utf-8")
        errors = []
        for f in files:
            if stop():
                raise RecompError("Stopped.")
            done = subprocess.run(limits.capped(["cc", "-fsyntax-only", "-w", "-I", str(include), "-include",
                                                 str(runtime), str(f)]), cwd=out / "funcs", capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=600)
            for line in done.stderr.splitlines():
                if "error:" not in line:
                    continue
                if (m := UNDECLARED.search(line)) and not second:
                    provided.add(m.group(1))
                elif m := CONFLICT.search(line):
                    clashes.add(m.group(1))
                else:
                    errors.append(line.strip())
        if not provided:
            break
    return sorted(provided), clashes, errors


def recompile(program: Path, rom_path: Path, segments: list[dict], functions: list[dict], out: Path, log=print,
              stop=lambda: False) -> dict:
    """Run N64Recomp over the split into `out` and check what it writes; returns (and writes to out/recomp.json) its
    summary."""
    rom = Path(rom_path).read_bytes()
    segments = [s for s in segments if s["rom_end"] > s["rom_start"]]
    usable = [f for f in functions if f["size"] > 0 and not f["vram"] & 3 and not f["size"] & 3 and not f["rom"] & 3]
    # a function between the split's segments (code the split left as data, matched C the project has for it
    # anyway) is a section of its own
    outside = sorted((f for f in usable if _segment(segments, f["vram"]) is None), key=lambda f: f["rom"])
    segments = segments + [{"name": f"between_{f['name']}", "rom_start": f["rom"], "rom_end": f["rom"] + f["size"],
                            "vram": f["vram"]} for f in outside]
    unusable = sorted({f["name"] for f in functions} - {f["name"] for f in usable})
    entry = _word(rom, 8)
    found = discover(rom, segments, usable, entry if _segment(segments, entry) else None)
    at_entry = any(f["vram"] == entry and f["rom"] == 0x1000 for f in usable + found)
    everything = usable + found
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "syms.toml").write_text(symbols(segments, everything), encoding="utf-8")
    log(f"N64Recomp: {len(usable)} of the split's functions, {len(found)} more found by the calls into them")
    refused: dict[str, str] = {}
    renamed: set[str] = set()
    include = Path(program).parent / "include"
    for _ in range(4):  # a name that clashes with the C library is renamed (name_recomp), and it runs again
        _translate(program, out, Path(rom_path).resolve(), entry if at_entry else None, refused, renamed, stop, log)
        log("checking the C with this computer's compiler")
        provided, clashes, errors = check(out, include, stop)
        if not clashes - renamed:
            break
        renamed |= clashes
    written = len(list((out / "funcs").glob("*.c")))
    summary = {"at": time.time(), "functions": len(usable), "found": [{k: f[k] for k in ("name", "vram", "size", "why")}
                                                                       for f in found],
               "refused": [{"name": n, "why": w} for n, w in refused.items()], "renamed": sorted(renamed),
               "runtime": provided, "errors": errors[:40], "error_count": len(errors),
               "between": [f["name"] for f in outside], "unusable": unusable, "files": written, "folder": str(out)}
    (out / "recomp.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    log(f"N64Recomp: {len(everything) - len(refused)} functions as C in {written} files; {len(refused)} set aside "
        f"as stubs, {len(found)} found that the split doesn't list, {len(provided)} for the runtime to provide; "
        + (f"{len(errors)} compile errors" if errors else "the C compiles"))
    return summary


def folder(root: Path) -> Path:
    return Path(root) / "build" / "recomp"


def load(root: Path) -> dict | None:
    """The last run's summary for a project folder, or None."""
    f = folder(root) / "recomp.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except ValueError:
        return None
