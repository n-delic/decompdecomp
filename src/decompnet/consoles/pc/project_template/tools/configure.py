#!/usr/bin/env python3
"""Write build.ninja and objdiff.json for this PC (Windows) decompilation, then run `ninja`.

    python tools/configure.py VERSION --msvc DIR [--msvc DIR ...] [--wibo PATH]

The build cuts every function out of your copy of the game (orig/, see config/VERSION/functions.json) into objects,
one per source file (tools/delink.py), and compiles src/ with the game's Visual C++ (run through wibo on Linux and
macOS): each file with its own version when config/VERSION/file_compilers.json names one, and with the more flags
config/VERSION/file_flags.json gives it (/Oy-: the frame pointer kept). objdiff.json (for objdiff and decompdecomp) compares every source file's functions with the original's.
`ninja rebuild` puts the whole program back together from them (tools/rebuild.py) and checks it against your copy.
Made by decompdecomp.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import ninja_syntax

# Settings (decompdecomp wrote these for this game; change them if the game was built differently)
MSVC_VERSION = "6.0"  # every file's, unless config/<version>/file_compilers.json names another for it
CL_FLAGS = ["/nologo", "/c", "/O2", "/W3", "/GX"]  # /GX: C++ exceptions, as games of the time were built

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
parser = argparse.ArgumentParser(description="Writes build.ninja and objdiff.json")
parser.add_argument("version", help="game version (a directory in config/)")
parser.add_argument("--msvc", required=True, action="append",
                    help="Visual C++ directory (holds 6.0/Bin/CL.EXE, 10.0SP1/Bin/cl.exe, ...); several: the first "
                         "with MSVC_VERSION")
parser.add_argument("--wibo", default="", help="runs the Windows compilers on Linux and macOS")
args = parser.parse_args()

VERSION = args.version
CONFIG = Path("config") / VERSION / "functions.json"
FILE_COMPILERS = CONFIG.parent / "file_compilers.json"  # {"src/auto/text_00401000.cpp": "7.1"}: files built with another
FILE_FLAGS = CONFIG.parent / "file_flags.json"  # {"src/auto/text_00401000.cpp": ["/Oy-"]}: files built with more flags
BUILD = Path("build") / VERSION


def q(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


# the Python the build's steps run with: on Windows python.exe, not the console-less pythonw.exe (under it, every
# compiler and linker a step starts opens a console window of its own)
_W = Path(sys.executable)
PYTHON = str(_W.with_name("python.exe")) if _W.name.lower() == "pythonw.exe" and _W.with_name("python.exe").is_file() \
    else sys.executable
PY = q([PYTHON]).replace("$", "$$")


def msvc(version: str) -> tuple[str, str]:
    """A Visual C++ version's compiler and its headers, from the first --msvc folder that has it."""
    homes = [Path(d) / version for d in args.msvc]
    home, cl = next(((h, p) for h in homes for p in (h / "Bin" / "CL.EXE", h / "Bin" / "cl.exe") if p.exists()),
                    (None, None))
    if cl is None:
        sys.exit(f"configure: Visual C++ {version} isn't in {' or '.join(args.msvc)} (file_compilers.json or "
                 "MSVC_VERSION)")
    return str(cl), str(home / "Include")


def file_compilers() -> dict[str, str]:
    return json.loads(FILE_COMPILERS.read_text(encoding="utf-8")) if FILE_COMPILERS.exists() else {}


def file_flags() -> dict[str, list[str]]:
    return json.loads(FILE_FLAGS.read_text(encoding="utf-8")) if FILE_FLAGS.exists() else {}


def files(top: str, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in Path(top).rglob("*") if p.suffix.lower() in suffixes)


def source_of(unit: str) -> str:
    """A unit's source file: C++ (.cpp), or C (.c) in projects made before decompdecomp wrote C++."""
    cpp = Path("src") / "auto" / f"{unit}.cpp"
    return cpp.as_posix() if cpp.exists() or not cpp.with_suffix(".c").exists() else cpp.with_suffix(".c").as_posix()


def main() -> None:
    info = json.loads(CONFIG.read_text(encoding="utf-8"))
    units = info["units"]
    headers = [p.as_posix() for p in files("include", (".h", ".hpp"))]
    with open("build.ninja", "w", encoding="utf-8") as f:
        n = ninja_syntax.Writer(f)
        n.rule("delink", f"{PY} tools/delink.py {CONFIG.as_posix()} {(BUILD / 'target').as_posix()}", restat=True)
        compiler = [PYTHON, "tools/compile.py", "--config", CONFIG.as_posix()]
        if args.wibo and sys.platform != "win32":
            compiler += ["--wrapper", args.wibo]
        # The runner invokes the compiler and names.py separately: cmd /c misparsed quoted compiler paths.
        n.rule("cl", q(compiler).replace("$", "$$") + " --compiler $cl --include $include --output $out --source $in -- "
               + q(CL_FLAGS).replace("$", "$$") + " $cflags")
        n.rule("configure", f"{PY} tools/configure.py " + q(sys.argv[1:]).replace("$", "$$"), generator=True)

        targets = [(BUILD / "target" / f"{u}.obj").as_posix() for u in units]
        n.build(targets, "delink", [CONFIG.as_posix(), "tools/delink.py"])
        objs = []
        versions, more = file_compilers(), file_flags()
        for src in files("src", (".c", ".cpp")):
            obj = (BUILD / src.with_suffix(".obj")).as_posix()
            src_posix = src.as_posix()
            cl, include = msvc(versions.get(src_posix, MSVC_VERSION))
            n.build(obj, "cl", src_posix, implicit=[*headers, CONFIG.as_posix(), "tools/compile.py", "tools/names.py"],
                    variables={"cl": q([cl]).replace("$", "$$"), "include": q([include]).replace("$", "$$"),
                               "cflags": q(more.get(src_posix, [])).replace("$", "$$")})
            objs.append(obj)
        n.build("all_source", "phony", objs)
        n.build("prepare", "phony", [*targets, *objs])
        # the whole program, from what src/ compiles to and the delinked functions (not the default: a build for
        # objdiff needs only the objects)
        # (each program where rebuild.py writes it: the game's DLLs too, functions.json's "modules")
        exes = [Path(info["exe"]).as_posix(), *(Path(m["exe"]).as_posix() for m in info.get("modules", []))]
        programs = [(BUILD / (Path(exe).relative_to("orig") if Path(exe).parts[0] == "orig" else Path(exe).name)).as_posix()
                    for exe in exes]
        n.rule("rebuild", f"{PY} tools/rebuild.py {CONFIG.as_posix()} {BUILD.as_posix()}")
        n.build(programs, "rebuild", [*targets, *objs],
                implicit=[CONFIG.as_posix(), *exes, "tools/rebuild.py", "tools/delink.py"])
        n.build("rebuild", "phony", programs)
        n.build("build.ninja", "configure", ["tools/configure.py", CONFIG.as_posix(), *headers,
                                             *[s.as_posix() for s in files("src", (".c", ".cpp"))],
                                             *[p.as_posix() for p in (FILE_COMPILERS, FILE_FLAGS) if p.exists()]])
        n.default(["prepare"])

    objdiff = {
        "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
        "custom_make": "ninja", "custom_args": [], "build_target": False, "build_base": True,
        "watch_patterns": ["*.c", "*.cpp", "*.h", "*.hpp"],
        "units": [{"name": u, "target_path": (BUILD / "target" / f"{u}.obj").as_posix(),
                   "base_path": (BUILD / "src" / "auto" / f"{u}.obj").as_posix(),
                   "metadata": {"source_path": source_of(u), "complete": False}} for u in units],
    }
    Path("objdiff.json").write_text(json.dumps(objdiff, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
