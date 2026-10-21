"""PlayStation (PS1): the R3000A's MIPS I (little-endian), Sony's PSY-Q GCCs with their assembler ASPSX, ROM mode.

PSY-Q compiled with GCC and assembled with ASPSX, which filled load delay slots and expanded divisions its own way:
each compiler here is the matching GCC whose assembly goes through maspsx (ASPSX's habits) and then GNU as
(toolchains.toml), as the community's PS1 decomps build.

- exe.py: the game's program (a PS-X EXE, e.g. SLUS_000.01) and the disc it comes on (SYSTEM.CNF's BOOT line); the
  disc itself is read with the PS2's ISO 9660 and CHD readers, and its overlays found with the PS2's overlay finder
  (consoles/ps2/disc.py, chd.py, overlays.py), in the PS1's RAM.
- guides/ps1.md: what agents read about matching PS1 code.

Its demo game is projects/demo-ps1 (the Demo Quest game built with GCC 2.7.2 and maspsx at -O2 -G8).
"""

from __future__ import annotations

from pathlib import Path

from ...arch import mips
from ...rom import Picked, RomError
from .. import BETA, EXPORT, ROM, Compiler, Console

SMALL = 64 << 20  # programs are read whole; disc images are read from disk
SMALL_DATA_BYTES = 8  # globals this small sit in $gp-relative small data when a game uses $gp (-G8)
BIOS_TABLES = {0xA0, 0xB0, 0xC0}  # the BIOS's function tables: a call jumps to one with the function's number in t1
LI_T2 = 0x240A0000  # addiu t2, zero, n (the table's address)
JR_T2 = 0x01400008


class PlayStation(Console):
    id = "ps"  # decomp.dev's id, which public PS1 decomps keep
    aliases = ("psx",)  # splat's, and decompdecomp's before 1.0.3
    name = "PlayStation"
    short = "PS1"
    status = BETA  # its demo matches and rebuilds; no real game has been rebuilt byte for byte whole yet
    arch = "mips"
    big_endian = False
    mode = ROM
    layout = "splat"
    compilers = (
        Compiler("gcc-2.7.2-psx", "GCC 2.7.2 (PSY-Q 4.0-4.3)", "most PlayStation games (1996-1998)", guess="PSYQ"),
        Compiler("gcc-2.6.3-psx", "GCC 2.6.3 (PSY-Q 3.5-3.6)", "early PlayStation games (1995-1996)"),
        Compiler("gcc-2.8.1-psx", "GCC 2.8.1 (PSY-Q 4.4+)", "late PlayStation games (1998-2000)"),
    )
    guides = ("mips", "ps1")
    compiler_guide = "ps1"
    m2c = "mipsel-gcc"
    ram = (0x80000000, 0x80200000)  # where its programs live: its 2 MiB, as the CPU's cached segment sees them
    demo = "demo-ps1"
    demo_tools = ("gcc-2.7.2-psx", "maspsx", "binutils-mips")
    own_features = frozenset({EXPORT})
    as_flags = ("-EL", "-march=r3000", "-mabi=32")
    image_ext = ".exe"

    def sniff(self, head: bytes, file) -> bool:
        from . import exe

        return exe.is_exe(head) or exe.is_ps1_disc(file)

    def read_game(self, file, name: str = "") -> Picked:
        """The program: a PS-X EXE the user picked, or the one a disc image boots (with the disc's overlays)."""
        from ..ps2 import disc
        from . import exe

        if isinstance(file, (str, Path)):
            path = Path(file)
            with open(path, "rb") as f:
                head = f.read(len(exe.MAGIC))
            if exe.is_exe(head) and path.stat().st_size <= SMALL:
                return self.read_game(path.read_bytes(), name or path.name)
        elif exe.is_exe(file):
            program, info = exe.read_exe(bytes(file), name)
            return Picked(self.id, program, "game.exe", info, False)
        with exe.opened(file) as src:
            if disc.is_disc(src):
                return self._from_disc(src)
        raise RomError("this isn't a PlayStation program (a PS-X EXE such as SLUS_000.01) or disc image")

    def _from_disc(self, src) -> Picked:
        import struct

        from ..ps2 import disc, overlays
        from . import exe

        image = disc._Disc(src)
        boot = exe.boot_name(image)
        if boot is None:
            raise RomError("this disc isn't a PlayStation game: its SYSTEM.CNF names no BOOT program")
        raw = image.find(boot)
        if raw is None:
            raise RomError(f"the disc names {boot} as its program, but it isn't on the disc")
        boot = boot.replace("\\", "/").rsplit("/", 1)[-1]
        if (loaded := exe.loaded_program(image, raw)) is not None:
            # the boot program is a loader (a 32 KB one on a PS1 game, the rest of it a picture): the game is the
            # program it loads. shortcut: the loader's own code is left out, the game's is what's worth decompiling
            path, raw = loaded
        program, info = exe.read_exe(raw, boot)  # (named as the disc boots it: its game code and region)
        if loaded is not None:
            info["program"] = path.replace("\\", "/")
        picked = Picked(self.id, program, "game.exe", info, False)
        main = (info["load"], info["load"] + info["size"] - exe.HEADER)
        try:
            picked.overlays = overlays.find(image, boot, main, self.ram)
        except (RomError, struct.error, IndexError):  # an odd disc: the program alone still works
            picked.overlays = []
        return picked

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """PSY-Q games that use $gp keep globals up to 8 bytes there (-G8); maspsx reads the same -G."""
        flags = super().default_flags(toolchain, gp)
        return [f for f in flags if not f.startswith("-G")] + [f"-G{SMALL_DATA_BYTES if gp else 0}"]

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        from ..ps2.overlays import add_to_config

        _place_text(config, (workdir / image).read_bytes(), log)
        return add_to_config(config, workdir, image, picked.overlays, log) if picked.overlays else image

    def find_gp(self, image: bytes, program: bytes, segments: list) -> int:
        from .exe import HEADER_FIELDS

        return mips.gp_from_entry(image, int.from_bytes(program[HEADER_FIELDS:HEADER_FIELDS + 4], "little"), segments)

    def after_import(self, net, slug: str, *, chosen: bool, log=print) -> None:
        """A PS-X EXE doesn't say which PSY-Q built it: unless the user picked one, the GCCs are tried on its code."""
        if not chosen:
            net.test_compilers(slug, [c.id for c in self.compilers], log=log)

    def asm_reason(self, code: bytes) -> str | None:
        words = [int.from_bytes(code[at:at + 4], "little") for at in range(0, min(len(code), 16) - 3, 4)]
        # a BIOS call: li t2, 0xA0/0xB0/0xC0, jr t2, li t1, n in any order. A C call through a pointer uses jalr and
        # passes its arguments in a0-a3, never a number in t1
        if {w & 0xFFFF for w in words if w & 0xFFFF0000 == LI_T2} & BIOS_TABLES and JR_T2 in words:
            return "calls the BIOS through its A0/B0/C0 table with the function's number in t1: a stub written in assembly"
        return mips.asm_reason(code, False, cop2="the GTE")


def _place_text(config: Path, program: bytes, log=print) -> None:
    """splat's guess at where the code is takes the first run of 33 varied instructions it can read: a picture in a
    program read that way (a loader's: 320 KB of 16-bit pixels, then its 32 KB of code), and its one "function" was
    there. When the code it guesses doesn't hold the entry point, .text is where the code's own marks say."""
    import struct

    import yaml

    from . import exe

    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    segments = cfg.get("segments", [])
    main = next((s for s in segments if isinstance(s, dict) and s.get("name") == "main"), None)
    if main is None or not main.get("subsegments"):
        return
    pc, _, load, _ = struct.unpack_from("<4I", program, exe.HEADER_FIELDS)
    entry = pc - load + exe.HEADER
    subs = main["subsegments"]
    end = next((s[0] if isinstance(s, list) else s.get("start") for s in segments[segments.index(main) + 1:]),
               len(program))
    starts = [s[0] for s in subs] + [end]
    if any(s[1] in ("c", "asm") and starts[i] <= entry < starts[i + 1] for i, s in enumerate(subs)):
        return
    if (found := exe.text_range(program)) is None or not found[0] <= entry < found[1]:
        return
    lo, hi = found
    main["subsegments"] = ([[exe.HEADER, "rodata", f"{exe.HEADER:X}"]] if lo > exe.HEADER else []) + [
        [lo, "c", "main"]] + ([[hi, "rodata", f"{hi:X}"]] if hi < end else [])
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding="utf-8")
    log(f"the code is at {lo:#x}-{hi:#x} of the program ({(hi - lo) // 1024} KiB), not where splat guessed")


PS1 = PlayStation()
