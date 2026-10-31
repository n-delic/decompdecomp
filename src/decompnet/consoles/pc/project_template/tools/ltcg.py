#!/usr/bin/env python3
"""Link-time code generation (LTCG) mode: build a source file the way a game compiled with /GL and linked with
/LTCG was built, so its functions can be compared with the original's.

    python tools/ltcg.py support OUT.lib --cl CL --lib LIB [--wibo WIBO]

Why: with /GL the compiler writes its intermediate code into the object, not machine code, and the linker generates
the code for the whole program at once. It then picks a function's calling convention from its callers (`this` or
arguments in EAX, ESI, EDI or EBX instead of ECX and the stack) and inlines across source files. So a function's
bytes depend on who calls it and on what it calls, and an object compiled on its own can't be compared at all.

The build links each source file in harness mode instead (tools/configure.py writes the rules from here):

- src/auto/<unit>.cpp, the decompiled code;
- harness/<unit>.drv.cpp, the driver: `extern "C" __declspec(dllexport) void HarnessEntry(...)` calling the unit's
  functions the way the original's call sites do (which registers hold what, which constants are passed), so the
  linker gives them the original's conventions;
- harness/<unit>.<anything>.cpp, stand-ins: the functions and data the unit uses from elsewhere in the game, with
  bodies shaped enough like the real ones (what they read and preserve) that the linker treats them alike. A file
  named <unit>.<name>.nogl.cpp is compiled without /GL: a stand-in for code the game's link couldn't see into
  (middleware, the XDK's own libraries).

All of it is compiled with /GL and linked into a DLL with /LTCG and a map by the same Visual C++'s own linker
(link.exe: through wine on Linux and macOS, which the user installs, as for Visual C++ 2017; wibo can't run it),
with a library of C runtime stubs (support.lib, built here) for what the compiler calls implicitly. A `// HARNESS-LINK: <flags>` line in any of those files adds linker flags for that unit, such as
`d3d8ltcg.lib` (the XDK's LTCG Direct3D, so its inlined code is generated alike) or `/d2:"-inlT56"`.
tools/dll2coff.py then cuts the unit's functions out of the DLL into the object objdiff compares.

A unit whose driver doesn't define HarnessEntry yet is compiled as a plain unit, without /GL: leaf functions with
standard conventions match that way too. Made by decompdecomp.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import compile as compiler_runner  # (tools/compile.py: how wine is found and started)

# The driver's entry point, defined (not commented out): a unit is built in harness mode once its driver has one.
ENTRY = re.compile(r'^[ \t]*extern[ \t]+"C"[^\n;]*\bHarnessEntry[ \t]*\(', re.M)
# a line of its own (a comment that only mentions the directive, as older driver skeletons did, isn't one)
LINK_LINE = re.compile(r"^[ \t]*//[ \t]*HARNESS-LINK:[ \t]*([^\r\n]*)", re.M)

# What the compiler calls without the source asking for it (floating point, 64-bit arithmetic, stack probes,
# pure virtual calls, structure copies). Builds link with /NODEFAULTLIB, so these stubs stand in for the C runtime:
# one object each in support.lib, so a stub is only pulled in when nothing else defines it. The 64-bit helpers are
# undecorated, take their operands on the stack and pop them (ret 16), as the C runtime's do.
CRT_STUBS = {
    "fltused": "int _fltused = 0x9875;\n",
    "purecall": "int __cdecl _purecall(void) { return 0; }\n",
    "ftol2": "long __cdecl _ftol2(void) { return 0; }\n",
    "chkstk": "void __cdecl _chkstk(void) { }\n",
    "allmul": "__declspec(naked) void _allmul(void) { __asm ret 16 }\n",
    "alldiv": "__declspec(naked) void _alldiv(void) { __asm ret 16 }\n",
    "allrem": "__declspec(naked) void _allrem(void) { __asm ret 16 }\n",
    "aulldiv": "__declspec(naked) void _aulldiv(void) { __asm ret 16 }\n",
    "aullrem": "__declspec(naked) void _aullrem(void) { __asm ret 16 }\n",
    "alldvrm": "__declspec(naked) void _alldvrm(void) { __asm ret 16 }\n",
    "aulldvrm": "__declspec(naked) void _aulldvrm(void) { __asm ret 16 }\n",
    "allshl": "__declspec(naked) void _allshl(void) { __asm ret }\n",
    "allshr": "__declspec(naked) void _allshr(void) { __asm ret }\n",
    "aullshr": "__declspec(naked) void _aullshr(void) { __asm ret }\n",
    "atexit": "int __cdecl atexit(void (__cdecl *f)(void)) { return f != 0; }\n",
    "memset": "void *__cdecl memset(void *d, int c, unsigned n) { char *p = (char *)d; while (n--) *p++ = (char)c;"
              " return d; }\n",
    "memcpy": "void *__cdecl memcpy(void *d, const void *s, unsigned n) { char *p = (char *)d;"
              " const char *q = (const char *)s; while (n--) *p++ = *q++; return d; }\n",
}
# /FORCE:UNRESOLVED: a unit links while some of its functions (or what they call) aren't written yet; references to
# them land at address 0, and dll2coff takes them as the original's (the functions using them say so)
LINK_FLAGS = ["/nologo", "/DLL", "/NOENTRY", "/NODEFAULTLIB", "/OPT:REF", "/LTCG", "/FORCE:UNRESOLVED"]
LINK_SECONDS = 900  # a whole program's code generation under wine
UNRESOLVED = re.compile(r"unresolved external symbol (?:\"[^\"]*\" \()?([^\s()\"]+)\)?")


def harness_files(unit: str, harness: Path = Path("harness")) -> tuple[Path, list[Path]]:
    """(driver, stand-ins) of a unit: harness/<unit>.drv.cpp and every other harness/<unit>.*.cpp."""
    driver = harness / f"{unit}.drv.cpp"
    extras = sorted(p for p in harness.glob(f"{unit}.*.cpp") if p != driver)
    return driver, extras


def has_entry(driver: Path) -> bool:
    try:
        return bool(ENTRY.search(driver.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def link_flags(paths: list[Path]) -> list[str]:
    """The extra linker flags of `// HARNESS-LINK:` lines. link.exe takes a single /d2:, so the last one wins."""
    out: list[str] = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in LINK_LINE.finditer(text):
            try:
                out += shlex.split(m.group(1).strip())
            except ValueError as e:  # an unclosed quote: say where, configure stops with it
                line = text.count("\n", 0, m.start()) + 1
                raise SystemExit(f"{p}:{line}: can't read the HARNESS-LINK flags ({e}): "
                                 f"{m.group(0).strip()}") from None
    d2 = [f for f in out if f.lower().startswith("/d2:")]
    return [f for f in out if not f.lower().startswith("/d2:")] + d2[-1:]


def write_rules(n, *, units: list[str], source_of, build: Path, config: Path, compiler_of, py: str, wibo: str,
                headers: list[str]) -> tuple[set[str], list[str]]:
    """Ninja rules and builds for every unit whose driver defines HarnessEntry. `compiler_of(source)`: the unit's
    (cl.exe, header folders, flags); its linker is the one beside the compiler. Returns (those units' source files,
    the harness files the build depends on, so editing a driver re-runs configure)."""
    q = subprocess.list2cmdline
    out = build / "ltcg"
    wrap = f'"{wibo}" ' if wibo and sys.platform != "win32" else ""
    n.rule("ltcg_cc", f'{wrap}"$cl" $flags /Iinclude /Iharness $includes /Fo$out $in', description="LTCG cc $in")
    n.rule("ltcg_support", f'{py} tools/ltcg.py support $out --cl "$cl" --lib "$lib"' + (f' --wibo "{wibo}"' if wrap else ""))
    # through `ltcg.py link`, which runs the linker (through wine) and keeps what it said (<map>.link.txt: what's
    # unresolved, for dll2coff)
    n.rule("ltcg_link", f'{py} tools/ltcg.py link $out $map.link.txt -- "$link" {q(LINK_FLAGS)} /MAP:$map /OUT:$out'
                        " $extra $in", description="LTCG link $out")
    n.rule("ltcg_coff", f"{py} tools/dll2coff.py {config.as_posix()} $unit $dll $map $out $sources")

    harness_paths = sorted(p.as_posix() for p in Path("harness").rglob("*") if p.is_file()) if Path("harness").is_dir() \
        else []
    done: set[str] = set()
    support_lib = None
    for unit in units:
        driver, extras = harness_files(unit)
        src = Path(source_of(unit))
        if not src.exists() or not has_entry(driver):
            continue
        cl, includes, flags = compiler_of(src.as_posix())
        beside = Path(cl).parent
        link = next((p for p in (beside / "link.exe", beside / "LINK.EXE") if p.exists()), beside / "link.exe")
        lib = next((p for p in (beside / "lib.exe", beside / "LIB.EXE") if p.exists()), beside / "lib.exe")
        if support_lib is None:  # shortcut: one support.lib for every unit, built by the first one's Visual C++
            support_lib = (out / "support.lib").as_posix()
            n.build(support_lib, "ltcg_support", implicit=["tools/ltcg.py"], variables={"cl": cl, "lib": str(lib)})
        gl = [f for f in flags if f.upper() != "/GL"] + ["/GL"]
        sources = [src, driver, *extras]
        objs = []
        for i, path in enumerate(sources):
            obj = (out / unit / f"{i:02d}_{path.stem.replace('.', '_')}.obj").as_posix()
            plain = [f for f in gl if f.upper() != "/GL"] if ".nogl." in path.name.lower() else gl
            n.build(obj, "ltcg_cc", path.as_posix(), implicit=headers,
                    variables={"cl": cl, "includes": q([f"/I{d}" for d in includes]), "flags": q(plain)})
            objs.append(obj)
        dll, mapf = (out / unit / f"{unit}.dll").as_posix(), (out / unit / f"{unit}.map").as_posix()
        n.build(dll, "ltcg_link", [*objs, support_lib], implicit=["tools/ltcg.py"],
                implicit_outputs=[mapf, f"{mapf}.link.txt"],
                variables={"map": mapf, "link": str(link), "extra": q(link_flags(sources))})
        base = (build / src.with_suffix(".obj")).as_posix()
        n.build([base], "ltcg_coff", [dll], implicit=[mapf, config.as_posix(), "tools/dll2coff.py", "tools/delink.py",
                                                      "tools/names.py", *(s.as_posix() for s in sources)],
                implicit_outputs=[Path(base).with_suffix(".ltcg.json").as_posix()],
                variables={"unit": unit, "dll": dll, "map": mapf, "sources": q([s.as_posix() for s in sources])})
        done.add(src.as_posix())
    return done, harness_paths


def _run(cmd: list[str], env: dict | None = None) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env)
    if res.returncode:
        sys.exit(f"{subprocess.list2cmdline(cmd)} failed:\n{res.stdout}{res.stderr}")


def support(out: Path, cl: str, lib: str, wibo: str) -> None:
    """support.lib: the C runtime stubs, one object each."""
    work = out.parent / "support"
    work.mkdir(parents=True, exist_ok=True)
    wrap = [wibo] if wibo and sys.platform != "win32" else []
    objs = []
    for name, body in CRT_STUBS.items():
        c, o = work / f"crt_{name}.c", work / f"crt_{name}.obj"
        c.write_text("#pragma function(memset, memcpy)\n" + body, encoding="utf-8")
        _run([*wrap, cl, "/nologo", "/c", "/O1", os.path.relpath(c), f"/Fo{os.path.relpath(o)}"])
        objs.append(os.path.relpath(o))
    # lib.exe, like link.exe, needs more of Windows than wibo has
    if sys.platform == "win32":
        _run([lib, "/nologo", f"/OUT:{os.path.relpath(out)}", *objs])
    else:
        wine, env = compiler_runner.wine_setup()
        _run([wine, lib, "/nologo", f"/OUT:{os.path.relpath(out)}", *objs], env)


def unresolved(log_text: str) -> list[str]:
    """The symbols a /FORCE:UNRESOLVED link left unresolved, from what it said."""
    return sorted(set(UNRESOLVED.findall(log_text)))


def link(out: str, log: str, cmd: list[str]) -> int:
    """Run the harness link, keep its output in `log`, show it. A /FORCE:UNRESOLVED link that made the DLL counts
    as done when the only errors are unresolved symbols: the unit's unwritten functions (and what only they call)
    aren't built yet, which shouldn't keep its written ones from being judged."""
    if os.path.exists(out):
        os.remove(out)
    env = None
    if sys.platform != "win32":  # Visual C++'s linker needs more of Windows than wibo has: wine runs it
        wine, env = compiler_runner.wine_setup()
        compiler_runner.keep_wine_server(wine, env)
        cmd = [wine, *cmd]
    # its output to a file, not a pipe (compile.py says why)
    with tempfile.TemporaryFile() as said:
        res = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=said, stderr=said, timeout=LINK_SECONDS)
        said.seek(0)
        text = said.read().decode("utf-8", "replace")
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    Path(log).write_text(text, encoding="utf-8")
    print(text.rstrip())
    if res.returncode == 0:
        return 0
    errors = [ln for ln in text.splitlines() if re.search(r"\berror LNK\d+", ln)]
    only_unresolved = errors and all(re.search(r"error LNK(2001|2019|1120)\b", ln) for ln in errors)
    if only_unresolved and os.path.exists(out):
        print(f"(linked with {len(unresolved(text))} unresolved: those functions aren't built yet)")
        return 0
    return res.returncode


def main() -> None:
    if len(sys.argv) > 4 and sys.argv[1] == "link" and sys.argv[4] == "--":
        sys.exit(link(sys.argv[2], sys.argv[3], sys.argv[5:]))
    p = argparse.ArgumentParser(description="LTCG harness mode helpers")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("support", help="build support.lib (C runtime stubs)")
    s.add_argument("out", type=Path)
    s.add_argument("--cl", required=True)
    s.add_argument("--lib", required=True)
    s.add_argument("--wibo", default="")
    a = p.parse_args()
    if a.cmd == "support":
        support(a.out, a.cl, a.lib, a.wibo)


if __name__ == "__main__":
    main()
