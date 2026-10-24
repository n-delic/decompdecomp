#!/usr/bin/env python3
"""Write build.ninja and objdiff.json for this PlayStation 4 decompilation.

    python tools/configure.py VERSION --clang PATH_TO_CLANG_5_0_2

The build cuts every function out of your own copy of the eboot (orig/, see config/VERSION/functions.json) into ELF64
objects, one per source file (tools/delink.py), and compiles src/ with the pinned clang. objdiff.json (for objdiff and
decompdecomp) compares every source file's functions with the original's. Made by decompdecomp.
"""

import argparse
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows has no flock
    fcntl = None

import ninja_syntax
from compiler_flags import CL_FLAGS

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
parser = argparse.ArgumentParser(description="Writes build.ninja and objdiff.json")
parser.add_argument("version", help="game version (a directory in config/)")
parser.add_argument("--clang", required=True, help="the pinned clang 5.0.2 to build src/ with")
args = parser.parse_args()

VERSION = args.version
CONFIG = Path("config") / VERSION / "functions.json"
BUILD = Path("build") / VERSION
CLANG = Path(args.clang)


def q(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


# the Python the build's steps run with: on Windows python.exe, not the console-less pythonw.exe
_W = Path(sys.executable)
PYTHON = str(_W.with_name("python.exe")) if _W.name.lower() == "pythonw.exe" and _W.with_name("python.exe").is_file() \
    else sys.executable
PY = q([PYTHON]).replace("$", "$$")


def write_atomic(path: Path, text: str) -> None:
    """Write beside the target and rename: parallel builds each run configure, and a file written in place gets
    interleaved into a corrupt one (ninja then refuses the whole project)."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def files(top: str, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in Path(top).rglob("*") if p.suffix.lower() in suffixes)


def source_of(unit: str) -> str:
    """A unit's source file."""
    c = Path("src") / "auto" / f"{unit}.c"
    return c.as_posix()


def main() -> None:
    # one generation at a time: every agent's build can trigger this rule, and two runs writing at once either
    # interleave (a corrupt file ninja cannot rebuild, because it cannot parse it to find the rule) or finish out
    # of order (a stale build.ninja replacing a fresh one)
    lock = open("build.ninja.lock", "w")
    if fcntl is not None:
        fcntl.flock(lock, fcntl.LOCK_EX)
    units = json.loads(CONFIG.read_text(encoding="utf-8"))["units"]
    headers = [str(p) for p in files("include", (".h", ".hpp"))]
    ninja_text = io.StringIO()  # the writer goes to memory, then one atomic rename
    if True:
        n = ninja_syntax.Writer(ninja_text)
        n.rule("delink", f"{PY} tools/delink.py {CONFIG} {BUILD / 'target'}", restat=True)
        compiler = [PYTHON, "tools/compile.py", "--compiler", str(CLANG), "--include", "include",
                    "--config", str(CONFIG)]
        n.rule("cc", q(compiler).replace("$", "$$") + " --output $out --source $in -- "
               + q(CL_FLAGS).replace("$", "$$") + " $cflags")
        n.rule("configure", f"{PY} tools/configure.py " + q(sys.argv[1:]).replace("$", "$$"), generator=True)

        targets = [str(BUILD / "target" / f"{u}.o") for u in units]
        n.build(targets, "delink", [str(CONFIG), "tools/delink.py"])
        objs = []
        for src in files("src", (".c", ".cpp")):
            obj = BUILD / src.with_suffix(".o")
            # Binding uses target symbols: its object must exist before the
            # compiler's post-processing, including the first parallel build.
            target = BUILD / "target" / f"{src.stem}.o"
            binding = [str(target)] if src.parent.as_posix() == "src/auto" and src.stem in units else []
            n.build(str(obj), "cc", str(src), implicit=[*headers, str(CONFIG), "tools/compile.py", "tools/bind.py", *binding])
            objs.append(str(obj))
        n.build("all_source", "phony", objs)
        n.build("prepare", "phony", [*targets, *objs])
        n.build("build.ninja", "configure", ["tools/configure.py", "tools/compiler_flags.py", str(CONFIG), *headers,
                                             *[str(s) for s in files("src", (".c", ".cpp"))]])
        n.default(["prepare"])
    write_atomic(Path("build.ninja"), ninja_text.getvalue())

    objdiff = {
        "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
        "custom_make": "ninja", "custom_args": [], "build_target": False, "build_base": True,
        "watch_patterns": ["*.c", "*.h", "*.hpp"],
        "options": {"functionRelocDiffs": "all", "combineDataSections": True, "combineTextSections": True},
        "units": [{"name": u, "target_path": str(BUILD / "target" / f"{u}.o"),
                   "base_path": str(BUILD / "src" / "auto" / f"{u}.o"),
                   "metadata": {"source_path": source_of(u), "complete": False}} for u in units],
    }
    write_atomic(Path("objdiff.json"), json.dumps(objdiff, indent=2))


if __name__ == "__main__":
    main()
