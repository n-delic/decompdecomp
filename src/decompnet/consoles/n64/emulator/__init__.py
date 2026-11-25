"""The reversing emulator: RetroReversing's mupen64plus core (mupenRE, with libRetroReversing), run headless to see
what the real game does where a port or a decompiled function behaves differently.

It runs the user's own ROM with scripted input, writes RDRAM at chosen vertical interrupts (big-endian, laid out like
the WebAssembly port's dumps, so the two compare word by word: `differences`), and reports which code writes an
address (write breakpoints) or what a function is called with (execution breakpoints). The V-Rally-like case that
motivated it: every car in a port started a race facing backwards; a write breakpoint on a car's matrix and an
execution breakpoint on its Euler-angle helper found the yaw the console computed, and the port's lost $v0.

Its sources are pinned in toolchains.toml ([mupenre]) and built here (`build`, called by toolchains.install) with
the pinned CMake and the host's C/C++ compiler: SDL2 and libpng (static), the core with its debugger, the HLE RSP,
and decompdecomp's own frontend and plugins in this folder (frontend.c, input.c, video.c, audio.c). What the frontend needs
from the core (its VI counter and hook, DecompGetViCount and DecompSetViHook; C++17 and arm64 builds) is in the
pinned commits themselves (RetroReversing/mupen64plus-core#2, RetroReversing/libRetroReversing#44).

usage: python -m decompnet.consoles.n64.emulator ROM --vis N [--input SCRIPT] [--dump VI:FILE] [--watch ADDR[:LEN]]
       [--break ADDR] [--from VI] [--expansion-pak]
"""
from __future__ import annotations

import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .... import limits

HERE = Path(__file__).resolve().parent
TOOL = "mupenre"
MAX_BREAKS = 16384  # execution breakpoints one run takes (the pinned core's, checked in constant time)
SO = "dylib" if sys.platform == "darwin" else "so"  # (not on Windows: the frontend uses dlopen)

class EmulatorError(RuntimeError):
    pass


OWN = ("frontend.c", "input.c", "video.c", "audio.c", "live.h")  # decompdecomp's own programs, built beside the pinned core


def _stamp() -> str:
    import hashlib
    return hashlib.sha256(b"".join((HERE / n).read_bytes() for n in OWN)).hexdigest()


def _programs(dest: Path, run=None) -> None:
    """Build the frontend and plugins (this folder's C) into an installed emulator, against its core's API headers."""
    def plain(argv, cwd, what):
        done = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=600)
        if done.returncode:
            raise EmulatorError(f"Building the reversing emulator's {what} failed: " + done.stderr[-1500:])
    run = run or plain
    api = dest / "include"
    plugin = ["cc", "-O2", "-fPIC", "-shared", "-I", api]
    run([*plugin, "-o", dest / f"input.{SO}", HERE / "input.c"], dest, "input plugin")
    run([*plugin, "-o", dest / f"video.{SO}", HERE / "video.c"], dest, "video plugin")
    run([*plugin, "-o", dest / f"audio.{SO}", HERE / "audio.c"], dest, "audio plugin")
    run(["cc", "-O2", "-I", api, "-o", dest / "mupenre-frontend", HERE / "frontend.c",
         *([] if sys.platform == "darwin" else ["-ldl"])], dest, "frontend")
    (dest / "programs.sha256").write_text(_stamp())


def current(folder: Path, log=print) -> Path:
    """The installed emulator, its frontend and plugins built again if this version's source differs from what built
    them (an install older than that, without the headers to build them, is installed again)."""
    stamp = folder / "programs.sha256"
    if stamp.is_file() and stamp.read_text().strip() == _stamp():
        return folder
    if (folder / "include" / "m64p_types.h").is_file():
        log("building the reversing emulator's frontend again (its source changed)")
        _programs(folder)
        return folder
    from .... import toolchains
    log("installing the reversing emulator again (its frontend changed)")
    return toolchains.install(TOOL, force=True, log=log)


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
        api = dest / "include"  # kept: the frontend and plugins are built again from them when their source changes
        shutil.copytree(core / "src" / "api", api, dirs_exist_ok=True)
        _programs(dest, run)
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
    recorded: list[Path] = field(default_factory=list)  # EMU_RECORD: each call's <path>.state and <path>.rdram
    frame_dumps: dict[int, Path] = field(default_factory=dict)  # EMU_FRAME_DUMP: {frame: file}


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
        elif parts[0] == "framedump" and len(parts) >= 3:
            out.frame_dumps[int(parts[1])] = Path(line.split(None, 2)[2])
        elif parts[0] == "recorded" and len(parts) >= 4:
            out.recorded.append(Path(line.split(None, 3)[3]))
        elif parts[0] == "stopped" and len(parts) == 2:
            out.vis = int(parts[1])
        elif parts[0] in ("error", "core:"):
            out.errors.append(line)
    return out


REPORT = ("watch ", "break ", "dumped ", "recorded ", "stopped ")


class _Output:
    """What a run printed, in bounded memory. The report lines parse() reads are all kept (EMU_MAX caps hits);
    anything else is kept up to NOISE characters, a line repeated in a row counted rather than stored. A homemade
    ROM the core can't run printed "core: not compiled exception" 800,000 times a second, 1.4 GB a minute, all kept:
    a test worker grew to 15 GB. The same line STUCK times in a row within STUCK_SECONDS, nothing else between, is a
    stuck emulator (a warning a game prints once a frame takes minutes to repeat that often): `stuck` names it and
    run() stops it."""
    NOISE = 256 * 1024
    STUCK = 10_000
    STUCK_SECONDS = 10.0

    def __init__(self, clock=time.monotonic):
        self.lines, self.noise, self.last, self.repeats, self.stuck = [], 0, None, 0, None
        self.clock, self.since, self.window = clock, 0.0, 0

    def add(self, line: str):
        if line.startswith(REPORT):
            self._flush()
            self.lines.append(line)
            return
        if line == self.last:
            self.repeats += 1
            self.window += 1
            if self.window >= self.STUCK:
                if self.clock() - self.since <= self.STUCK_SECONDS:
                    self.stuck = line.strip()
                self.since, self.window = self.clock(), 0  # (a slow repeat: judge the next STUCK afresh)
            return
        self._flush()
        self.last, self.repeats, self.window, self.since = line, 1, 1, self.clock()
        if self.noise < self.NOISE:
            self.lines.append(line)
            self.noise += len(line)

    def _note(self) -> str:
        return f"(the line above {self.repeats - 1:,} more times)\n" if self.repeats > 1 else ""

    def _flush(self):
        if (note := self._note()) and self.noise < self.NOISE:
            self.lines.append(note)
            self.noise += len(note)
        self.last, self.repeats = None, 0

    def text(self) -> str:
        return "".join(self.lines) + (self._note() if self.noise < self.NOISE else "")


def installed() -> Path | None:
    from .... import toolchains

    return toolchains.path(TOOL) if toolchains.installed(TOOL) else None


_auto = {"lock": threading.Lock(), "failed": 0.0}
RETRY_AFTER = 3600  # seconds before a failed build is tried again, not once per import


def ensure(log=print) -> Path | None:
    """`installed()`, after downloading the pinned sources and building the emulator when it is missing, as
    objdiff-cli, wibo and decomp-permuter are. Without it an import silently skipped running the game (loaded_code),
    so the code a game loads at run time stayed data: a computer that never built it imported worse than one that
    had. None when it can't be built here (no build for this computer, DECOMP_AUTO_TOOLS=off, or the build failed:
    reported to `log`, retried after RETRY_AFTER seconds)."""
    from .... import toolchains

    if (found := installed()) is not None:
        return found
    if os.environ.get("DECOMP_AUTO_TOOLS", "on") == "off" or not toolchains.get(TOOL).supported:
        return None
    with _auto["lock"]:
        if (found := installed()) is not None:
            return found
        if _auto["failed"] and time.monotonic() - _auto["failed"] < RETRY_AFTER:
            return None
        try:
            log("Building the reversing emulator (mupenRE) from its pinned source; this happens once.")
            toolchains.ensure(TOOL, log=log)
        except Exception as error:  # imports go on without running the game, and say so
            _auto["failed"] = time.monotonic()
            log(f"The reversing emulator could not be built ({str(error)[:200]}); the game isn't run to find its loaded code.")
            return None
    return installed()


def run(rom: Path, vis: int, *, inputs: str = "", dumps: dict[int, Path] | None = None,
        watches: list[tuple[int, int]] | tuple = (), breaks: list[int] | tuple = (), report_from: int = 0,
        max_hits: int = 200, expansion_pak: bool = False, timeout: float = 1800,
        record: tuple[Path, int] | None = None, stop=lambda: False, on_record=None, pending: int = 0,
        frame_dumps: dict[int, Path] | None = None, input_clock: str = "vi", extra_env: dict[str, str] | None = None) -> Run:
    """Run the user's ROM in the reversing emulator for `vis` vertical interrupts. `inputs`: the input script
    (input.c), timed in retraces or, with `input_clock` "frames", in the game's frames (its graphics tasks); `dumps`:
    {vi: file}; `frame_dumps`: {frame: file}, RDRAM as the game hands over that graphics task; `watches`: (physical address, length) write breakpoints; `breaks`: virtual
    addresses to stop at (breakpoints need the slower pure interpreter)."""
    folder = installed()
    if folder is None:
        raise EmulatorError("The reversing emulator isn't installed: install mupenRE under Compilers & tools → Tools (it builds from its source).")
    env = {**os.environ, "EMU_VIS": str(vis), "EMU_MAX": str(max_hits), "EMU_FROM": str(report_from)}
    for key in ("EMU_INPUT", "EMU_DUMP", "EMU_WATCH", "EMU_BREAK", "EMU_8MB", "EMU_FRAME_DUMP", "EMU_INPUT_CLOCK"):
        env.pop(key, None)
    if input_clock == "frames":
        env["EMU_INPUT_CLOCK"] = "frames"
    env.update(extra_env or {})  # (settings of the video plugin's own: EMU_TASK_LOG, EMU_VI_LOG; a job's thread sets none of os.environ)
    if frame_dumps:
        env["EMU_FRAME_DUMP"] = ",".join(f"{n}:{Path(p).resolve()}" for n, p in sorted(frame_dumps.items()))
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
    if record is not None:  # (folder, calls): the state at each of the first calls to the breaks
        env["EMU_RECORD"] = f"{Path(record[0]).resolve()}:{int(record[1])}"
        env.pop("EMU_MAX", None)
        if pending:  # the frontend waits while this many recordings are left: on_record takes (and deletes) them
            env["EMU_RECORD_PENDING"] = str(int(pending))
    folder = current(folder)
    with tempfile.TemporaryDirectory(prefix="decomp-emulator-") as tmp:  # its configuration and anything it writes
        env["EMU_CONFIG"] = tmp
        argv = [folder / "mupenre-frontend", folder / f"libmupen64plus.{SO}", folder / f"rsp-hle.{SO}",
                folder / f"input.{SO}", folder / f"video.{SO}", Path(rom).resolve()]
        proc = subprocess.Popen(limits.capped([str(a) for a in argv]), cwd=tmp, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        started, cut = time.monotonic(), False
        # line by line: a run cut short keeps what it reported (a killed run's child can hold the pipe open)
        lines: queue.Queue = queue.Queue()

        def read():
            for line in proc.stderr:
                lines.put(line)
            lines.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        ended = False
        kept = _Output()
        while not ended:  # the run, ended early by `stop` (a user's Stop) or the timeout
            if stop() or time.monotonic() - started > timeout:
                cut = True
                proc.kill()
                break
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                ended = True
                continue
            kept.add(line)
            if kept.stuck:
                proc.kill()
                break
            if on_record is not None and line.startswith("recorded "):
                on_record(Path(line.rstrip("\n").split(None, 3)[3]))  # (meanwhile the emulation goes on, or waits)
        proc.wait()
        reader.join(2)
        while not lines.empty() and not kept.stuck:
            line = lines.get()
            if line is not None:
                kept.add(line)
        text = kept.text()
    if kept.stuck:
        raise EmulatorError(f"The emulator is stuck repeating {kept.stuck!r} and was stopped "
                            f"(after {kept.repeats:,} times, frame {parse(text).vis or 0}).")
    result = parse(text)
    if cut:
        result.timed_out = True
        if stop():
            raise EmulatorError("Stopped.")
        return result
    if proc.returncode and not result.vis:
        raise EmulatorError("The emulator stopped: " + ("; ".join(result.errors) or text[-400:]))
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


@dataclass
class Recording:
    """One recorded call (`<base>.state`, `<base>.rdram`): the registers at the function's entry and, on demand, the
    RAM there (4 or 8 MB, big-endian from 0x80000000; read where asked, never held)."""
    base: Path
    pc: int
    gpr: list[int]
    fpr: list[int]

    def read(self, address: int, width: int = 4) -> int | None:
        """The `width` bytes (1, 2, 4 or 8) at a virtual address before the call, or None outside RAM."""
        offset = address & 0x1FFFFFFF
        try:
            with open(f"{self.base}.rdram", "rb") as dump:
                dump.seek(offset)
                data = dump.read(width)
        except OSError:
            return None
        return int.from_bytes(data, "big") if len(data) == width else None


def read_recording(base: Path) -> Recording:
    """The registers of a recorded call: its state file is the pc, the 32 general registers, hi, lo and a few more
    words, then the 32 floating-point registers (the layout behaviour.py's `_state` reads)."""
    words = Path(f"{base}.state").read_text().split()
    return Recording(Path(base), int(words[0], 16), [int(w, 16) for w in words[1:33]],
                     [int(w, 16) for w in words[35:67]])


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


__all__ = ["EmulatorError", "Hit", "Recording", "Run", "build", "differences", "main", "parse", "read_recording", "run",
           "word"]
