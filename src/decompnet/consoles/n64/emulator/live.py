"""The emulator played live, for the side-by-side viewer (port/wasm/side_by_side.py): it runs in real time with the pad
taken from a shared file instead of a script, and writes its RAM into that file as the game hands over each graphics
task (live.h has the layout; keep the two in step). The viewer draws the frames from it and sets the pad.

    shared = Shared(folder / "live.bin")
    proc = start(rom, shared)          # a Popen; kill it to stop
    shared.snapshot()                  # (frames, vi, task, ram) of the newest whole frame, or None
    shared.set_pad(buttons, x, y)      # the port's own button masks (A is 0x8000), the stick -80..80
"""
from __future__ import annotations

import mmap
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .... import limits
from . import SO, EmulatorError, current, installed

HEADER = 256
RAM_MAX = 8 << 20
VIRAM = HEADER + RAM_MAX
AUDIO = VIRAM + RAM_MAX
RING = 1 << 17  # stereo frames
TOTAL = AUDIO + 4 * RING
SEQ, FRAMES, VI, GATE, TASK, VISEQ, VIVI, AUDIO_HZ, AUDIO_END, INPUT = 0, 4, 8, 12, 16, 80, 84, 88, 92, 128  # (the task is 64 bytes at 16)
FREE = 0xFFFFFFFF  # the gate of a run that isn't in lockstep
sys_order = sys.byteorder  # the header's words are the host's


def pad_value(buttons: int, x: int, y: int) -> int:
    """The input plugin's word for the port's controller state: the console's bytes the other way round (the port and
    libultra's OSContPad hold A as 0x8000, the core's BUTTONS has it in bit 7), the stick in the top two bytes."""
    swapped = (buttons & 0xFF) << 8 | (buttons >> 8 & 0xFF)
    return swapped | (x & 0xFF) << 16 | (y & 0xFF) << 24


class Shared:
    """The shared file, made here and mapped by both sides."""

    def __init__(self, path: Path, expansion_pak: bool = False, lockstep: bool = False):
        self.path = Path(path)
        self.ram_size = (8 if expansion_pak else 4) << 20
        with self.path.open("w+b") as f:
            f.truncate(TOTAL)
            self.map = mmap.mmap(f.fileno(), TOTAL)
        self.lockstep = lockstep
        self.set_gate(0 if lockstep else FREE)  # lockstep: the emulator parks at its first retrace

    def reset(self) -> None:
        """As new, for another emulator run on the same file (the last one must have ended): no frame, no retrace
        published, no sound, the pad released."""
        self.map[:HEADER] = bytes(HEADER)
        self.set_gate(0 if self.lockstep else FREE)

    def _word(self, at: int) -> int:
        return int.from_bytes(self.map[at:at + 4], sys_order)

    def snapshot(self, tries: int = 4):
        """(frames, vi, task, ram) as of the newest whole frame: read between two equal, even sequence numbers, the
        emulator's write seqlock. None while it has written nothing, or kept writing through every try."""
        for _ in range(tries):
            before = self._word(SEQ)
            if before == 0 or before & 1:
                time.sleep(0.002)
                continue
            frames, vi = self._word(FRAMES), self._word(VI)
            task = bytes(self.map[TASK:TASK + 64])
            ram = bytes(self.map[HEADER:HEADER + self.ram_size])
            if self._word(SEQ) == before:
                return frames, vi, task, ram
        return None

    def audio(self, after: int) -> bytes:
        """The console's sound since frame `after` (-1: just the latest tenth of a second), in the page's format: the rate
        (u32), the number of the first frame (u64), 4 spare bytes, then 16-bit little-endian stereo frames, as the port's
        own live view serves its sound (live.c /audio)."""
        hz = self._word(AUDIO_HZ) or 22050
        end = self._word(AUDIO_END)
        start = max(0, end - RING // 2)
        if after < 0:
            start = max(0, end - hz // 10)
        elif after > start:
            start = min(after, end)
        frames = end - start
        first, last = start % RING, end % RING
        if frames == 0:
            data = b""
        elif first < last:
            data = bytes(self.map[AUDIO + 4 * first:AUDIO + 4 * last])
        else:
            data = bytes(self.map[AUDIO + 4 * first:AUDIO + 4 * RING]) + bytes(self.map[AUDIO:AUDIO + 4 * last])
        return struct.pack("<IQI", hz, start, 0) + data

    def set_gate(self, vi: int) -> None:
        """Lockstep: the emulator may complete every retrace up to `vi`."""
        self.map[GATE:GATE + 4] = (vi & 0xFFFFFFFF).to_bytes(4, sys_order)

    def parked(self) -> int:
        """The retrace the emulator last published its RAM at (0: none yet)."""
        return self._word(VIVI) if self._word(VISEQ) & 1 == 0 else 0

    def retrace_ram(self, vi: int) -> bytes | None:
        """The RAM at retrace `vi`, when that is the one the emulator parked at; None otherwise (or while it writes)."""
        before = self._word(VISEQ)
        if before == 0 or before & 1 or self._word(VIVI) != vi:
            return None
        ram = bytes(self.map[VIRAM:VIRAM + self.ram_size])
        return ram if self._word(VISEQ) == before else None

    def set_pad(self, buttons: int, x: int, y: int) -> None:
        self.map[INPUT:INPUT + 4] = (pad_value(buttons, x, y) & 0xFFFFFFFF).to_bytes(4, sys_order)

    def close(self) -> None:
        self.map.close()



def start(rom: Path, shared: Shared, *, expansion_pak: bool = False, saves: Path | None = None) -> subprocess.Popen:
    """The emulator running `rom` live, writing to and reading from `shared`; its configuration folder goes when it
    ends. `saves`: the folder the game's saves are kept in (else the emulator's usual one in the user's home).
    Raises EmulatorError when it isn't installed."""
    folder = installed()
    if folder is None:
        raise EmulatorError("The reversing emulator isn't installed: install mupenRE under Compilers & tools → Tools (it builds from its source).")
    folder = current(folder)
    env = {**os.environ, "EMU_LIVE": str(shared.path.resolve())}
    for key in ("EMU_INPUT", "EMU_DUMP", "EMU_WATCH", "EMU_BREAK", "EMU_FRAME_DUMP", "EMU_INPUT_CLOCK", "EMU_VIS", "EMU_8MB", "EMU_SAVES", "EMU_UNPACED"):
        env.pop(key, None)
    if expansion_pak:
        env["EMU_8MB"] = "1"
    if shared.lockstep:  # the viewer's gate paces it
        env["EMU_UNPACED"] = "1"
    if saves is not None:
        Path(saves).mkdir(parents=True, exist_ok=True)
        env["EMU_SAVES"] = str(Path(saves).resolve())
    tmp = tempfile.TemporaryDirectory(prefix="decomp-emulator-")
    env["EMU_CONFIG"] = tmp.name
    argv = [folder / "mupenre-frontend", folder / f"libmupen64plus.{SO}", folder / f"rsp-hle.{SO}",
            folder / f"input.{SO}", folder / f"video.{SO}", Path(rom).resolve(), folder / f"audio.{SO}"]
    proc = subprocess.Popen(limits.capped([str(a) for a in argv]), cwd=tmp.name, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    proc._config_dir = tmp  # kept until the process object goes
    return proc
