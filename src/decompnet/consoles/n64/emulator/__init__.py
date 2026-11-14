"""The reversing emulator: RetroReversing's mupen64plus core (mupenRE, with libRetroReversing), run headless to see
what the real game does where a port or a decompiled function behaves differently.

It runs the user's own ROM with scripted input, writes RDRAM at chosen vertical interrupts (big-endian, laid out like
the WebAssembly port's dumps, so the two compare word by word: `differences`), and reports which code writes an
address (write breakpoints) or what a function is called with (execution breakpoints). The V-Rally-like case that
motivated it: every car in a port started a race facing backwards; a write breakpoint on a car's matrix and an
execution breakpoint on its Euler-angle helper found the yaw the console computed, and the port's lost $v0.

Its sources are pinned in toolchains.toml ([mupenre]) and built here (`build`, called by toolchains.install) with
the pinned CMake and the host's C/C++ compiler: SDL2 and libpng (static), the core with its debugger, the HLE RSP,
and decompdecomp's own frontend and plugins in this folder (frontend.c, input.c, video.c). What the frontend needs
from the core (its VI counter and hook, DecompGetViCount and DecompSetViHook; C++17 and arm64 builds) is in the
pinned commits themselves (RetroReversing/mupen64plus-core#2, RetroReversing/libRetroReversing#44).

usage: python -m decompnet.consoles.n64.emulator ROM --vis N [--input SCRIPT] [--dump VI:FILE] [--watch ADDR[:LEN]]
       [--break ADDR] [--from VI] [--expansion-pak]
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .... import limits

HERE = Path(__file__).resolve().parent
TOOL = "mupenre"
SO = "dylib" if sys.platform == "darwin" else "so"  # (not on Windows: the frontend uses dlopen)

class EmulatorError(RuntimeError):
    pass


def _host_cpu() -> str:
    machine = platform.machine().lower()
    return {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)


def build(dest: Path, cmake: Path, log=print) -> None:
    """Build the emulator from the pinned sources unpacked in dest/source into dest (toolchains.install calls it)."""
    mac = sys.platform == "darwin"
    hint = ("Xcode Command Line Tools (`xcode-select --install`)" if mac else
            "a C/C++ compiler, make and zlib headers (on Debian or Ubuntu: `sudo apt install g++ make zlib1g-dev`)")
    if not shutil.which("make") or not shutil.which("cc") or not shutil.which("c++"):
        raise EmulatorError(f"The reversing emulator is built from its source on this computer, which needs {hint}.")
    source = dest / "source"
    jobs = str(min(8, os.cpu_count() or 1))

    def run(argv, cwd, what):
        try:
            done = subprocess.run(limits.capped([str(a) for a in argv]), cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EmulatorError(f"Building the reversing emulator ({what}) failed: {error}. It needs {hint}.") from error
        if done.returncode:
            raise EmulatorError(f"Building the reversing emulator ({what}) failed. It needs {hint}.\n"
                                + (done.stdout + done.stderr)[-3000:])
        return done.stdout

    with tempfile.TemporaryDirectory(prefix="decomp-mupenre-") as tmp:
        prefix = Path(tmp) / "prefix"
        common = ["-G", "Unix Makefiles", "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={prefix}",
                  "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"]
        log("  building the reversing emulator from its pinned source (first install only, a few minutes)")
        for name, options in (("sdl2", ["-DSDL_SHARED=OFF", "-DSDL_STATIC=ON", "-DSDL_STATIC_PIC=ON", "-DSDL_TEST=OFF"]),
                              ("libpng", ["-DPNG_SHARED=OFF", "-DPNG_STATIC=ON", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF",
                                          "-DPNG_FRAMEWORK=OFF"])):
            out = Path(tmp) / name
            run([cmake, "-S", source / name, "-B", out, *common, *options], tmp, f"configuring {name}")
            run([cmake, "--build", out, "--parallel", jobs], tmp, f"building {name}")
            run([cmake, "--install", out], tmp, f"installing {name}")
        core = source / "core"
        sdl_libs = run(["sh", prefix / "bin" / "sdl2-config", "--static-libs"], tmp, "reading SDL2's libraries").split()
        lib = next((prefix / d for d in ("lib", "lib64") if (prefix / d / "libpng16.a").is_file()), prefix / "lib")
        unix = core / "projects" / "unix"
        run(["make", "all", f"-j{jobs}", f"HOST_CPU={_host_cpu()}", "NO_ASM=1", "OSD=0", "DEBUGGER=1",
             "PKG_CONFIG=false", f"SDL_CFLAGS=-I{prefix / 'include' / 'SDL2'} -D_THREAD_SAFE",
             "SDL_LDLIBS=" + " ".join(sdl_libs), f"LIBPNG_CFLAGS=-I{prefix / 'include'}",
             f"LIBPNG_LDLIBS={lib / 'libpng16.a'}", "ZLIB_CFLAGS=", "ZLIB_LDLIBS=-lz"], unix, "the core")
        built = next(iter(sorted(unix.glob(f"libmupen64plus*.{SO}*"))), None)
        if built is None:
            raise EmulatorError(f"The reversing emulator's core built, but its library isn't in {unix}.")
        rsp = source / "rsp-hle" / "projects" / "unix"
        run(["make", "all", f"-j{jobs}", f"HOST_CPU={_host_cpu()}", f"APIDIR={core / 'src' / 'api'}"], rsp, "the RSP")
        api = core / "src" / "api"
        plugin = ["cc", "-O2", "-fPIC", "-shared", "-I", api]
        run([*plugin, "-o", dest / f"input.{SO}", HERE / "input.c"], tmp, "the input plugin")
        run([*plugin, "-o", dest / f"video.{SO}", HERE / "video.c"], tmp, "the video plugin")
        run(["cc", "-O2", "-I", api, "-o", dest / "mupenre-frontend", HERE / "frontend.c",
             *([] if mac else ["-ldl"])], tmp, "the frontend")
        shutil.copyfile(built, dest / f"libmupen64plus.{SO}")
        shutil.copyfile(rsp / f"mupen64plus-rsp-hle.{SO}", dest / f"rsp-hle.{SO}")
    shutil.rmtree(source, ignore_errors=True)  # what runs is kept; the pinned archives stay in .downloads


@dataclass
class Hit:
    """A breakpoint hit: `watch` (a write to a watched address, before the store) or `break` (execution reached a
    watched address; `words` are the four words at $a0 then)."""
    kind: str
    vi: int
    pc: int
    ra: int
    args: tuple[int, int, int, int]
    v0: int
    words: tuple[int, ...] = ()


@dataclass
class Run:
    vis: int = 0
    hits: list[Hit] = field(default_factory=list)
    dumps: dict[int, Path] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False  # stopped by the timeout before its last VI (a game that never starts its video counts none)


def parse(log: str) -> Run:
    """The frontend's report (frontend.c's header lists its lines)."""
    out = Run()
    for line in log.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] in ("watch", "break") and len(parts) >= 9:
            numbers = [int(p, 16) for p in parts[2:]]
            out.hits.append(Hit(parts[0], int(parts[1]), numbers[0], numbers[1], tuple(numbers[2:6]), numbers[6],
                                tuple(numbers[7:])))
        elif parts[0] == "dumped" and len(parts) >= 3:
            out.dumps[int(parts[1])] = Path(line.split(None, 2)[2])
        elif parts[0] == "stopped" and len(parts) == 2:
            out.vis = int(parts[1])
        elif parts[0] in ("error", "core:"):
            out.errors.append(line)
    return out


def installed() -> Path | None:
    from .... import toolchains

    return toolchains.path(TOOL) if toolchains.installed(TOOL) else None


def run(rom: Path, vis: int, *, inputs: str = "", dumps: dict[int, Path] | None = None,
        watches: list[tuple[int, int]] | tuple = (), breaks: list[int] | tuple = (), report_from: int = 0,
        max_hits: int = 200, expansion_pak: bool = False, timeout: float = 1800) -> Run:
    """Run the user's ROM in the reversing emulator for `vis` vertical interrupts. `inputs`: the input script
    (input.c); `dumps`: {vi: file}; `watches`: (physical address, length) write breakpoints; `breaks`: virtual
    addresses to stop at (breakpoints need the slower pure interpreter)."""
    folder = installed()
    if folder is None:
        raise EmulatorError("The reversing emulator isn't installed: install mupenRE under Compilers & tools → Tools (it builds from its source).")
    env = {**os.environ, "EMU_VIS": str(vis), "EMU_MAX": str(max_hits), "EMU_FROM": str(report_from)}
    for key in ("EMU_INPUT", "EMU_DUMP", "EMU_WATCH", "EMU_BREAK", "EMU_8MB"):
        env.pop(key, None)
    if inputs:
        env["EMU_INPUT"] = inputs
    if dumps:
        env["EMU_DUMP"] = ",".join(f"{vi}:{Path(p).resolve()}" for vi, p in sorted(dumps.items()))
    if watches:
        env["EMU_WATCH"] = ",".join(f"{a & 0x1FFFFFFF:X}:{n:X}" for a, n in watches)
    if breaks:
        env["EMU_BREAK"] = ",".join(f"{a & 0xFFFFFFFF:X}" for a in breaks)
    if expansion_pak:
        env["EMU_8MB"] = "1"
    with tempfile.TemporaryDirectory(prefix="decomp-emulator-") as tmp:  # its configuration and anything it writes
        env["EMU_CONFIG"] = tmp
        argv = [folder / "mupenre-frontend", folder / f"libmupen64plus.{SO}", folder / f"rsp-hle.{SO}",
                folder / f"input.{SO}", folder / f"video.{SO}", Path(rom).resolve()]
        try:
            done = subprocess.run(limits.capped([str(a) for a in argv]), cwd=tmp, env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as error:  # what it reported until then still counts
            text = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) else error.stderr or ""
            result = parse(text)
            result.timed_out = True
            return result
    result = parse(done.stderr)
    if done.returncode and not result.vis:
        raise EmulatorError("The emulator stopped: " + ("; ".join(result.errors) or done.stderr[-400:]))
    return result


def word(dump: bytes, address: int) -> int:
    """A word of an RDRAM dump (the emulator's or the port's: big-endian from 0x80000000)."""
    o = address & 0x1FFFFFFC
    return int.from_bytes(dump[o:o + 4], "big") if o + 4 <= len(dump) else 0


def differences(a: bytes, b: bytes, start: int = 0x80000000, end: int = 0x80400000, gap: int = 16):
    """The ranges where two RDRAM dumps differ, as (first address, last address + 4, words that differ), words
    within `gap` bytes of each other in one range."""
    out = []
    lo, hi = start & 0x1FFFFFFC, min(end & 0x1FFFFFFF or 0x20000000, len(a), len(b))
    base = start - lo
    current = None
    for o in range(lo, hi - 3, 4):
        if a[o:o + 4] == b[o:o + 4]:
            continue
        if current and o - current[1] <= gap:
            current[1], current[2] = o + 4, current[2] + 1
        else:
            if current:
                out.append(current)
            current = [o, o + 4, 1]
    if current:
        out.append(current)
    return [(base + s, base + e, n) for s, e, n in out]


def _address(text: str) -> int:
    return int(text, 16)


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m decompnet.consoles.n64.emulator",
                                description="Run your N64 ROM headless in the reversing emulator.")
    p.add_argument("rom", type=Path)
    p.add_argument("--vis", type=int, required=True, help="vertical interrupts to run (60 a second)")
    p.add_argument("--input", default="", help="vi:BUTTON+BUTTON+SX=n+SY=n,... or @file")
    p.add_argument("--dump", action="append", default=[], metavar="VI:FILE")
    p.add_argument("--watch", action="append", default=[], metavar="ADDR[:LEN]")
    p.add_argument("--break", dest="breaks", action="append", default=[], metavar="ADDR")
    p.add_argument("--from", dest="report_from", type=int, default=0, metavar="VI")
    p.add_argument("--max", type=int, default=200)
    p.add_argument("--expansion-pak", action="store_true")
    p.add_argument("--timeout", type=float, default=1800, help="seconds before the run is cut short")
    args = p.parse_args(argv)
    inputs = Path(args.input[1:]).read_text().strip() if args.input.startswith("@") else args.input
    dumps = {int(d.split(":", 1)[0]): Path(d.split(":", 1)[1]) for d in args.dump}
    watches = [(_address(w.split(":")[0]), int(w.split(":")[1], 16) if ":" in w else 4) for w in args.watch]
    result = run(args.rom, args.vis, inputs=inputs, dumps=dumps, watches=watches,
                 breaks=[_address(b) for b in args.breaks], report_from=args.report_from, max_hits=args.max,
                 expansion_pak=args.expansion_pak, timeout=args.timeout)
    for h in result.hits:
        extra = (" [a0] " + " ".join(f"{w:08X}" for w in h.words)) if h.words else ""
        print(f"{h.kind} VI {h.vi} pc {h.pc:08X} ra {h.ra:08X} a0-a3 "
              + " ".join(f"{x:08X}" for x in h.args) + f" v0 {h.v0:08X}{extra}")
    for vi, path in sorted(result.dumps.items()):
        print(f"dumped VI {vi}: {path}")
    print(f"stopped after {result.vis} VIs" + (" (timed out)" if result.timed_out else ""))
    return 0


__all__ = ["EmulatorError", "Hit", "Run", "build", "differences", "main", "parse", "run", "word"]
