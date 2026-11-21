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

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from ... import limits
from ...forks.csource import _blank

TOOL = "n64recomp"
JR_RA = 0x03E00008
MAX_PROBE_RUNS = 64  # one crash's controlled body subsets


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
# a clash with the C library's names, or with `main`, whose parameters C fixes (a game's own function named main,
# as a racing game's first C function is): renamed, as N64Recomp's `renamed` does, rather than set aside
CONFLICT = re.compile(r"(?:conflicting types for|redefinition of) '(\w+)'|(?:parameter|argument) of '(main)'|'(main)' (?:must|is not|takes)")
_UNKNOWN_STUB = re.compile(r"Function (\S+) is stubbed out in the config file but does not exist!")


def _crash(code: int) -> str | None:
    """Input-related native crashes only. A killed or terminated process may be a resource limit or Stop."""
    for name in ("SIGSEGV", "SIGBUS", "SIGABRT", "SIGILL"):
        if (value := getattr(signal, name, None)) is not None and code == -value:
            return name
    return "access violation" if code in (0xC0000005, -1073741819) else None


def _failure(done) -> str:
    text = (done.stdout + done.stderr).strip().splitlines()
    reason = text[-1] if text else "no diagnostic output"
    return f"N64Recomp stopped ({_crash(done.returncode) or f'exit {done.returncode}'}): {reason}"


def _function_names(out: Path, entry) -> set[str]:
    """Names after the CLI's entry-point rename, so disabling a body really disables that body."""
    sections = tomllib.loads((out / "syms.toml").read_text(encoding="utf-8"))["section"]
    return {"recomp_entrypoint" if entry is not None and f["vram"] == entry
            and section["rom"] + f["vram"] - section["vram"] == 0x1000 else f["name"]
            for section in sections for f in section.get("functions", [])}


def _translate(program, out, rom, entry, refused, renamed, stop, log):
    """Translate everything the CLI can handle, retaining explicit reasons for normal refusals and native crashes.

    Its jump-table analyser can read outside the ROM and segfault without a diagnostic. Controlled body subsets
    isolate such a crash: all other bodies become stubs, then the single body must reproduce it. A global crash,
    a resource kill or an inconsistent result still fails; a partial output file is never taken as evidence.
    """
    runs = full_runs = 0
    probe_names = None
    patchable = _function_names(out, entry)
    # Each full failed pass must refuse a new input body. Scale to the game instead of cutting a user's job off
    # after 400 functions; probes are separately bounded for each body, and repeated failures stop immediately.
    full_limit = len(patchable) + 1
    # Name resolution may try and then drop one alternate spelling per input name.
    total_limit = full_limit * (MAX_PROBE_RUNS + 1) + 2 * len(patchable)

    def invoke(extra=(), *, probe=False):
        nonlocal runs, full_runs
        if stop():
            raise RecompError("Stopped.")
        if runs >= total_limit or (not probe and full_runs >= full_limit):
            raise RecompError("N64Recomp reached its translation safety limit: check the split and retry")
        runs += 1
        full_runs += not probe
        (out / "recomp.toml").write_text(config(rom, entry, sorted(set(refused) | set(extra)), sorted(renamed)),
                                         encoding="utf-8")
        shutil.rmtree(out / "funcs", ignore_errors=True)
        (out / "funcs").mkdir(parents=True)
        return subprocess.run(limits.capped([str(program), "recomp.toml"]), cwd=out, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)

    def refusal(done):
        text = done.stdout + done.stderr
        failed = next((m.group(1) for line in text.splitlines() if (m := ERROR.match(line))), None)
        if failed is None or failed in refused or failed not in patchable:
            return False
        reason = next((line for line in text.splitlines() if line.strip() and not ERROR.match(line)
                       and not line.startswith(("Error in recompiling", "Function count", "Working dir", "["))), "")
        refused[failed] = reason.strip()
        if len(refused) % 10 == 0:
            log(f"N64Recomp: {len(refused)} functions set aside so far")
        return True

    def input_failure(done):
        if crashed := _crash(done.returncode):
            return "crash", crashed
        text = done.stdout + done.stderr
        failed = next((m.group(1) for line in text.splitlines() if (m := ERROR.match(line))), None)
        if failed is not None and failed not in patchable:
            # The CLI also invents static function bodies that its patches table cannot name. Do not put an
            # unpatchable generated name in stubs: isolate the source body that causes it to be generated instead.
            return "generated body", failed
        return None

    def isolate(label):
        nonlocal probe_names
        if probe_names is None:
            probe_names = _function_names(out, entry)
        names = probe_names
        if not names:
            return None
        probes = 0

        def probe(active):
            nonlocal probes
            if probes >= MAX_PROBE_RUNS:
                raise RecompError("N64Recomp reached its crash-isolation safety limit: check the split and retry")
            probes += 1
            # The CLI validates stub names before translating any body. Resolving those names and recording
            # ordinary refusals can take several invocations before this subset yields evidence about the crash.
            while True:
                done = invoke(names - active, probe=True)
                if done.returncode == 0:
                    return False
                if failure := input_failure(done):
                    return failure
                unknown = _UNKNOWN_STUB.search(done.stdout + done.stderr)
                if unknown and unknown[1] in names - active:
                    # The CLI renames built-in library functions before reading patches. Use that name once;
                    # if it is absent too, leave it outside the probe set rather than inventing a body/refusal.
                    names.remove(unknown[1])
                    if not unknown[1].endswith("_recomp"):
                        names.add(unknown[1] + "_recomp")
                        patchable.add(unknown[1] + "_recomp")
                    continue
                if not _crash(done.returncode) and refusal(done):
                    continue  # a normal refusal must not mask a later crash in the same subset
                raise RecompError(_failure(done))

        if probe(set()):
            return None  # even disabling every body crashes: no single function has been implicated
        suspects = sorted(names - set(refused))
        log(f"N64Recomp {label}; isolating the affected function")
        while len(suspects) > 1:
            half = len(suspects) // 2
            if probe(set(suspects[:half])):
                suspects = suspects[:half]
            elif probe(set(suspects[half:])):
                suspects = suspects[half:]
            else:
                return None  # the crash needs an interaction we have not proved safe to set aside
        if suspects and (stopped_by := probe(set(suspects))):
            return suspects[0], stopped_by
        return None

    while True:
        done = invoke()
        if done.returncode == 0:
            return
        if not _crash(done.returncode) and refusal(done):
            continue
        if failure := input_failure(done):
            kind, detail = failure
            label = f"crashed with {detail}" if kind == "crash" else f"could not translate generated body {detail}"
            if isolated := isolate(label):
                failed, (kind, detail) = isolated
                reason = (f"crashed with {detail}" if kind == "crash" else f"could not translate generated body {detail}")
                refused[failed] = f"N64Recomp {reason} translating this body alone; explicit runtime stub"
                log(f"N64Recomp: {failed} set aside after a confirmed {kind}: {detail}")
                continue
        raise RecompError(_failure(done))


def check(out: Path, include: Path, stop=lambda: False) -> tuple[list[str], set[str], list[str], set[str]]:
    """The output checked with this computer's C compiler, as a port would build it (syntax only, nothing runs).
    Returns the functions the runtime has to provide (N64Recomp leaves out the libultra ones its runtime
    reimplements: declared in funcs/runtime.h), names that clash with the C library, other errors, and function
    definitions in translation units that passed the final syntax check."""
    files = sorted((out / "funcs").glob("*.c"))
    runtime = out / "funcs" / "runtime.h"
    provided: set[str] = set()
    clashes: set[str] = set()
    errors: list[str] = []
    for second in (False, True):
        passed: set[str] = set()
        runtime.write_text("#include \"recomp.h\"\n" + "".join(
            f"RECOMP_FUNC void {n}(uint8_t* rdram, recomp_context* ctx);\n" for n in sorted(provided)), encoding="utf-8")
        errors = []
        for f in files:
            if stop():
                raise RecompError("Stopped.")
            done = subprocess.run(limits.capped(["cc", "-fsyntax-only", "-w", "-I", str(include), "-include",
                                                 str(runtime), str(f)]), cwd=out / "funcs", capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=600)
            if done.returncode == 0:
                passed.update(DEFINED.findall(f.read_text(encoding="utf-8", errors="replace")))
            for line in done.stderr.splitlines():
                if "error:" not in line:
                    continue
                if (m := UNDECLARED.search(line)) and not second:
                    provided.add(m.group(1))
                elif m := CONFLICT.search(line):
                    clashes.add(next(g for g in m.groups() if g))
                else:
                    errors.append(line.strip())
        if not provided:
            break
    return sorted(provided), clashes, errors, passed


def _invalid_bodies(out: Path, errors: list[str]) -> dict[str, str]:
    """Generated function bodies containing compiler errors. Header errors never implicate an arbitrary body."""
    files, bad = {}, {}
    folder = (out / "funcs").resolve()
    for error in errors:
        match = re.match(r"^(.*?):(\d+):\d+: error: (.*)", error)
        if match is None:
            continue
        file = Path(match[1]).resolve()
        if file.parent != folder or file.suffix != ".c":
            continue
        if file not in files:
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            files[file] = (lines, [(n, m[1]) for n, line in enumerate(lines, 1)
                                  if (m := re.match(r"^RECOMP_FUNC void (\w+)\(", line))])
        lines, definitions = files[file]
        number = int(match[2])
        body = next(((line, name) for line, name in reversed(definitions) if line <= number), None)
        if body:
            prefix = _blank("\n".join(lines[body[0]-1:number]))
            if prefix.count("{") > prefix.count("}"):
                bad[body[1]] = match[3][:300]
    return bad


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
    # An interrupted rebuild leaves generated files without a completed summary.
    (out / "recomp.json").unlink(missing_ok=True)
    (out / "syms.toml").write_text(symbols(segments, everything), encoding="utf-8")
    log(f"N64Recomp: {len(usable)} of the split's functions, {len(found)} more found by the calls into them")
    refused: dict[str, str] = {}
    renamed: set[str] = set()
    include = Path(program).parent / "include"
    for _ in range(4):  # a name that clashes with the C library is renamed (name_recomp), and it runs again
        _translate(program, out, Path(rom_path).resolve(), entry if at_entry else None, refused, renamed, stop, log)
        log("checking the C with this computer's compiler")
        provided, clashes, errors, passed = check(out, include, stop)
        known = _function_names(out, entry if at_entry else None)
        bad = {name: reason for name, reason in _invalid_bodies(out, errors).items()
               if name in known and name not in refused}
        for name, reason in bad.items():
            refused[name] = f"the generated C does not compile: {reason}; explicit runtime stub"
        if bad:
            log(f"N64Recomp: {len(bad)} more bodies set aside because their generated C does not compile")
        if not clashes - renamed and not bad:
            break
        renamed |= clashes
    # Generated stubs can pass the syntax check. Count only requested bodies, including the CLI's renamed symbols.
    translated = {name if name in known else name.removesuffix("_recomp") for name in passed - refused.keys()}
    succeeded = len((translated & known) - refused.keys())
    written = len(list((out / "funcs").glob("*.c")))
    summary = {"at": time.time(), "image_sha1": hashlib.sha1(rom).hexdigest(), "functions": len(usable),
               "found": found, "succeeded": succeeded,
               "refused": [{"name": n, "why": w} for n, w in refused.items()], "renamed": sorted(renamed),
               "runtime": provided, "errors": errors[:40], "error_count": len(errors),
               "between": [f["name"] for f in outside], "unusable": unusable, "files": written, "folder": str(out)}
    pending = out / "recomp.json.pending"
    pending.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    pending.replace(out / "recomp.json")
    log(f"N64Recomp: {succeeded} of {len(everything)} functions recompiled and passed the C check in {written} files; {len(refused)} set aside "
        f"as stubs, {len(found)} found that the split doesn't list, {len(provided)} for the runtime to provide; "
        + (f"{len(errors)} compile errors" if errors else "the C compiles"))
    return summary


def folder(root: Path) -> Path:
    return Path(root) / "build" / "recomp"


def load(root: Path) -> dict | None:
    """The last run's summary for a project folder, or None."""
    f = folder(root) / "recomp.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


DEFINED = re.compile(r"^RECOMP_FUNC void (\w+)\(", re.M)
SYMBOL = re.compile(r'\{ name = ("(?:[^"\\]|\\.)*"), vram = 0x([0-9A-Fa-f]+), size = 0x[0-9A-Fa-f]+ \}')


def harness(out: Path, entry: int, log=print, stop=lambda: False) -> Path:
    """A program that runs one recompiled function from a recorded state (recomp_runtime.c), built from the last
    Recompile's output in `out` (objects kept: only what changed is built again). Returns its path."""
    import concurrent.futures
    import hashlib
    out = Path(out)
    summary = json.loads((out / "recomp.json").read_text(encoding="utf-8"))
    funcs = sorted((out / "funcs").glob("funcs_*.c"))
    defined = set()
    for f in funcs:
        defined.update(DEFINED.findall(f.read_text(encoding="utf-8", errors="replace")))
    table = {}
    for m in SYMBOL.finditer((out / "syms.toml").read_text(encoding="utf-8")):
        name, vram = json.loads(m.group(1)), int(m.group(2), 16)
        c = next((n for n in (name, f"{name}_recomp", "recomp_entrypoint" if vram == entry else None)
                  if n and n in defined), None)
        if c and vram not in table:
            table[vram] = c
    declared = set(re.findall(r"void (\w+)\(", (out / "funcs" / "funcs.h").read_text(encoding="utf-8")))
    # what N64Recomp leaves to its runtime (the libultra it reimplements, declared but never defined): stubs that
    # only say they were reached
    stubs = sorted((set(summary.get("runtime") or []) | declared) - defined)
    lines = ['#include "runtime.h"', '#include "funcs.h"', "void harness_runtime(const char *name);",
             "typedef struct { uint32_t vram; recomp_func_t *func; } HarnessFunction;"]
    lines += [f'void {n}(uint8_t* rdram, recomp_context* ctx) {{ (void)rdram; (void)ctx; harness_runtime("{n}"); }}'
              for n in stubs]
    lines += [f"void {c}(uint8_t* rdram, recomp_context* ctx);" for c in sorted(set(table.values()) - declared)]
    lines.append("const HarnessFunction harness_functions[] = {")
    lines += [f"    {{0x{v:08X}u, {c}}}," for v, c in sorted(table.items())]
    lines += ["};", f"const unsigned harness_function_count = {len(table)};"]
    build = out / "harness"
    build.mkdir(exist_ok=True)
    (build / "harness_table.c").write_text("\n".join(lines) + "\n", encoding="utf-8")
    include = Path(toolchain_folder()) / "include"
    flags = ["-O1", "-w", "-I", str(include), "-I", str(out / "funcs"), "-include", str(out / "funcs" / "runtime.h")]
    header = (include / "recomp.h").read_bytes() + (out / "funcs" / "runtime.h").read_bytes() + \
        ((out / "funcs" / "funcs.h").read_bytes() if (out / "funcs" / "funcs.h").is_file() else b"")
    units = [*funcs, build / "harness_table.c", Path(__file__).with_name("recomp_runtime.c")]

    def compile_one(unit: Path) -> str | None:
        obj = build / (unit.stem + ".o")
        key = hashlib.sha256(unit.read_bytes() + header + " ".join(flags).encode()).hexdigest()
        stamp = obj.with_suffix(".key")
        if obj.is_file() and stamp.is_file() and stamp.read_text() == key:
            return None
        if stop():
            return "stopped"
        done = subprocess.run(limits.capped(["cc", *flags, "-c", str(unit), "-o", str(obj)]), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=1800)
        if done.returncode:
            return f"{unit.name}: " + (done.stderr.strip().splitlines() or ["failed"])[0]
        stamp.write_text(key)
        return None
    log(f"building the harness that runs one recompiled function ({len(units)} files, only what changed)")
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        failed = [e for e in pool.map(compile_one, units) if e]
    if failed:
        raise RecompError("Building the recompiled functions' harness failed: " + "; ".join(failed[:3]))
    program = build / "run-one"
    done = subprocess.run(limits.capped(["cc", "-o", str(program), *(str(build / (u.stem + ".o")) for u in units)]),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if done.returncode:
        raise RecompError("Linking the recompiled functions' harness failed: " + done.stderr.strip()[-800:])
    return program


def toolchain_folder() -> Path:
    from ... import toolchains
    return toolchains.path(TOOL)


def run_one(program: Path, vram: int, state: Path, rdram: Path, out_prefix: Path, timeout: float = 60) -> dict:
    """One recorded call through the recompiled function: {"ok", "v0", "v1", "f0", "f1", "runtime", "error",
    "rdram"} (rdram: the memory after it, big-endian)."""
    after, result = Path(f"{out_prefix}.rdram"), Path(f"{out_prefix}.result")
    try:
        done = subprocess.run([str(program), f"{vram:08X}", str(state), str(rdram), str(after), str(result)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"didn't return within {timeout:.0f} s"}
    if done.returncode or not result.is_file():
        return {"ok": False, "error": (done.stderr.strip().splitlines() or [f"stopped ({done.returncode})"])[-1]}
    lines = result.read_text().splitlines()  # "runtime <name>" as the call reached each, then the registers
    rest = [line for line in lines if line.startswith("runtime ")]
    last = next((line for line in reversed(lines) if line and not line.startswith("runtime ")), None)
    if last is None:
        return {"ok": False, "error": "the harness wrote no result"}
    v0, v1, f0, f1 = (int(x, 16) for x in last.split())
    return {"ok": True, "v0": v0, "v1": v1, "f0": f0, "f1": f1, "rdram": after,
            "runtime": sorted({line.split()[1] for line in rest if line.startswith("runtime ")})}
