#!/usr/bin/env python3
"""Write build.ninja and objdiff.json for this PC (Windows) decompilation, then run `ninja`.

    python tools/configure.py VERSION --msvc DIR [--wibo PATH]

The build cuts every function out of your copy of the game (orig/, see config/VERSION/functions.json) into objects,
one per source file (tools/delink.py), and compiles src/ with the game's Visual C++ (run through wibo on Linux and
macOS). objdiff.json (for objdiff and decompdecomp) compares every source file's functions with the original's.
Made by decompdecomp.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import ninja_syntax

# Settings (decompdecomp wrote these for this game; change them if the game was built differently)
MSVC_VERSION = "6.0"
CL_FLAGS = ["/nologo", "/c", "/O2", "/W3", "/GX"]  # /GX: C++ exceptions, as games of the time were built

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
parser = argparse.ArgumentParser(description="Writes build.ninja and objdiff.json")
parser.add_argument("version", help="game version (a directory in config/)")
parser.add_argument("--msvc", required=True, help="Visual C++ directory (holds 6.0/Bin/CL.EXE, 7.1/Bin/cl.exe, ...)")
parser.add_argument("--wibo", default="", help="runs the Windows compilers on Linux and macOS")
args = parser.parse_args()

VERSION = args.version
CONFIG = Path("config") / VERSION / "functions.json"
BUILD = Path("build") / VERSION
MSVC = Path(args.msvc) / MSVC_VERSION
CL = next((p for p in (MSVC / "Bin" / "CL.EXE", MSVC / "Bin" / "cl.exe") if p.exists()), MSVC / "Bin" / "CL.EXE")
WRAP = f'"{args.wibo}" ' if args.wibo and sys.platform != "win32" else ""
PY = f'"{sys.executable}"'
SH = "cmd /c " if sys.platform == "win32" else ""  # ninja starts commands without a shell: && needs one on Windows


def q(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


def files(top: str, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in Path(top).rglob("*") if p.suffix.lower() in suffixes)


def source_of(unit: str) -> str:
    """A unit's source file: C++ (.cpp), or C (.c) in projects made before decompdecomp wrote C++."""
    cpp = Path("src") / "auto" / f"{unit}.cpp"
    return cpp.as_posix() if cpp.exists() or not cpp.with_suffix(".c").exists() else cpp.with_suffix(".c").as_posix()


def main() -> None:
    units = json.loads(CONFIG.read_text(encoding="utf-8"))["units"]
    headers = [str(p) for p in files("include", (".h", ".hpp"))]
    with open("build.ninja", "w", encoding="utf-8") as f:
        n = ninja_syntax.Writer(f)
        n.rule("delink", f"{PY} tools/delink.py {CONFIG} {BUILD / 'target'}", restat=True)
        # then the original's names on what it compiled to (C++ mangles them: tools/names.py)
        n.rule("cl", f'{SH}{WRAP}"{CL}" {q(CL_FLAGS)} /Iinclude "/I{MSVC / "Include"}" $cflags /Fo$out $in'
               f' && {PY} tools/names.py {CONFIG} $out')
        n.rule("configure", f"{PY} tools/configure.py {q(sys.argv[1:])}", generator=True)

        targets = [str(BUILD / "target" / f"{u}.obj") for u in units]
        n.build(targets, "delink", [str(CONFIG), "tools/delink.py"])
        objs = []
        for src in files("src", (".c", ".cpp")):
            obj = BUILD / src.with_suffix(".obj")
            n.build(str(obj), "cl", str(src), implicit=[*headers, str(CONFIG), "tools/names.py"])
            objs.append(str(obj))
        n.build("all_source", "phony", objs)
        n.build("prepare", "phony", [*targets, *objs])
        n.build("build.ninja", "configure", ["tools/configure.py", str(CONFIG), *headers,
                                             *[str(s) for s in files("src", (".c", ".cpp"))]])
        n.default(["prepare"])

    objdiff = {
        "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
        "custom_make": "ninja", "custom_args": [], "build_target": False, "build_base": True,
        "watch_patterns": ["*.c", "*.cpp", "*.h", "*.hpp"],
        "units": [{"name": u, "target_path": str(BUILD / "target" / f"{u}.obj"),
                   "base_path": str(BUILD / "src" / "auto" / f"{u}.obj"),
                   "metadata": {"source_path": source_of(u), "complete": False}} for u in units],
    }
    Path("objdiff.json").write_text(json.dumps(objdiff, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
