"""PlayStation 2: the Emotion Engine's MIPS (R5900, little-endian), Sony's EE GCC and CodeWarrior compilers, the
kernel's calls, ROM mode.

- elf.py: the game's program (an ELF, e.g. SLUS_200.00) and which compiler its code looks built with.
- disc.py, chd.py: the disc image the program comes on (ISO 9660 as .iso or raw .bin, or a CHD), read in place.
- overlays.py: code the game keeps in other files of its disc, placed at the address it's loaded at.
- syscalls.py: names for the kernel's call stubs (FlushCache, CreateThread...), from the number each one loads.
- symtab.py, mdebug.py, stabs.py, cheader.py, debuginfo.py: a game's own debug symbols, when it shipped with them
  (#3): names, source files, prototypes and types, read from its symbol table and its STABS (in .mdebug).
- guides/ps2.md: what agents read about matching PS2 code.

Its demo game is projects/demo-ps2 (the Demo Quest game built with EE-GCC 2.96, other compilers and -g too).
"""

from __future__ import annotations

from pathlib import Path

from ...arch import mips
from ...rom import Picked, RomError
from .. import BETA, EXPORT, ROM, Compiler, Console

SMALL = 64 << 20  # programs are read whole; disc images are read from disk
ELF_ENTRY = slice(24, 28)  # e_entry in a 32-bit ELF header
SMALL_DATA_BYTES = 8  # globals this small sit in $gp-relative small data when a game uses $gp


class PlayStation2(Console):
    id = "ps2"
    name = "PlayStation 2"
    short = "PS2"
    status = BETA  # tested on real games (GTA San Andreas, Ridge Racer V), not yet rebuilt byte for byte whole
    arch = "mips"
    big_endian = False
    mode = ROM
    layout = "splat"
    compilers = (
        Compiler("ee-gcc-2.96", "EE-GCC 2.96", "PlayStation 2 games (Sony's EE GCC)", guess="EEGCC"),
        Compiler("ee-gcc-2.9-991111", "EE-GCC 2.9", "launch-era PlayStation 2 games (the first SDK's GCC)",
                 guess="EEGCC-2.9"),
        Compiler("ee-gcc-2.95.2", "EE-GCC 2.95.2 (SN)", "PlayStation 2 games built with SN Systems' ProDG",
                 guess="EEGCC-2.95.2"),
        Compiler("ee-gcc-2.95.3", "EE-GCC 2.95.3 (SN)", "later PlayStation 2 games built with SN Systems' ProDG",
                 guess="EEGCC-2.95.3"),
        Compiler("ee-gcc-3.2", "EE-GCC 3.2", "late PlayStation 2 games (2003-2005)", guess="EEGCC-3.2"),
        Compiler("mwcps2-2.3.3", "CodeWarrior PS2 2.3.3", "PlayStation 2 games built with CodeWarrior (2000-2001, "
                 "proprietary)", guess="MWCPS2-2"),
        Compiler("mwcps2-3.0.1", "CodeWarrior PS2 3.0.1", "PlayStation 2 games built with CodeWarrior (2002-2005, "
                 "proprietary)", guess="MWCPS2-3"),
    )
    guides = ("mips", "ps2")
    compiler_guide = "ps2"
    m2c = "mipsee-gcc"  # m2c has no CodeWarrior MIPS target: the EE one reads its code as well
    ram = (0x00080000, 0x02000000)  # where the EE's programs live: above the kernel, within its 32 MiB
    demo = "demo-ps2"
    demo_tools = ("ee-gcc-2.96", "binutils-mips")
    own_features = frozenset({EXPORT})
    as_flags = ("-EL", "-march=r5900", "-mabi=eabi")

    def sniff(self, head: bytes, file) -> bool:
        from . import chd, disc, elf

        return elf.is_mips_elf(head) or chd.is_chd(head) or disc.is_disc(head)

    def read_game(self, file, name: str = "") -> Picked:
        """The program: an ELF the user picked, or the one a disc image names (with the disc's overlays)."""
        from . import chd, disc, elf

        if isinstance(file, (str, Path)):
            path = Path(file)
            name = name or path.name
            with open(path, "rb") as f:
                head = f.read(8)
            if chd.is_chd(head):  # a compressed disc image: read in place, a hunk at a time
                with chd.Chd(path) as image:
                    if disc.is_disc(image):
                        return self._from_disc(image)
                raise RomError("this CHD doesn't hold a PlayStation 2 disc this app can read")
            if path.stat().st_size <= SMALL:
                return self.read_game(path.read_bytes(), name)
            with open(path, "rb") as f:
                if disc.is_disc(f):
                    return self._from_disc(f)
            raise RomError("this file is too big for a program and isn't a disc image this app can read")
        if elf.is_mips_elf(file):
            program, info = elf.read_elf(bytes(file), name)
            return Picked(self.id, program, "game.elf", info, False)
        if disc.is_disc(file):
            return self._from_disc(file)
        raise RomError("this isn't a PlayStation 2 program (an ELF such as SLUS_200.00) or disc image")

    def _from_disc(self, src) -> Picked:
        import struct

        from . import disc, elf, overlays

        raw, boot = disc.from_disc(src)
        if not elf.is_mips_elf(raw):
            raise RomError(f"the disc's program {boot} isn't a PlayStation 2 program this app can read")
        program, info = elf.read_elf(raw, boot)
        picked = Picked(self.id, program, "game.elf", info, False)
        segs = overlays.elf_segments(raw) or []
        main = (min((a for a, _ in segs), default=0), max((a + len(d) for a, d in segs), default=0))
        try:
            picked.overlays = overlays.find(disc._Disc(src), boot, main)
        except (RomError, struct.error, IndexError):  # an odd disc: the program alone still works
            picked.overlays = []
        return picked

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """PlayStation 2 games keep globals up to 8 bytes $gp-relative when they use $gp (GCC's -G8; CodeWarrior's
        -sdatathreshold 8)."""
        flags = super().default_flags(toolchain, gp)
        small = str(SMALL_DATA_BYTES if gp else 0)
        own = ["-sdatathreshold", small] if toolchain.startswith("mwcps2") else [f"-G{small}"]
        return [f for f in flags if not f.startswith("-G")] + own

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        from .overlays import add_to_config

        return add_to_config(config, workdir, image, picked.overlays, log) if picked.overlays else image

    def find_gp(self, image: bytes, program: bytes, segments: list) -> int:
        return mips.gp_from_entry(image, int.from_bytes(program[ELF_ENTRY], "little"), segments)

    def debug_info(self, program: bytes, known: set[str]):
        from . import debuginfo

        return debuginfo.read(program, known)

    def debug_brief(self, known: dict) -> list[str]:
        from . import debuginfo

        return debuginfo.brief_lines(known)

    def after_import(self, net, slug: str, *, chosen: bool, log=print) -> None:
        """What a program says about its compiler can mislead (GTA San Andreas keeps one library's "MW MIPS C
        Compiler" note; its own code is EE-GCC 2.96's): unless the user picked one, the GCCs are tried on the code.
        CodeWarrior isn't: the test compiles m2c's drafts, which are GCC-style, and CodeWarrior never matches them."""
        if not chosen and net.cfg(net.project_row(slug))["rom"]["compiler_guess"].startswith("EEGCC"):
            net.test_compilers(slug, [c.id for c in self.compilers if c.guess.startswith("EEGCC")], log=log)
        name_kernel_calls(net, slug, log=log)

    def on_start(self, net, slug: str, log=print) -> None:
        name_kernel_calls(net, slug, log=log)  # projects imported before their kernel calls were named at import

    def asm_reason(self, code: bytes) -> str | None:
        return mips.asm_reason(code, False, eabi=True, cop2="VU0")


def name_kernel_calls(net, slug: str, log=print) -> int:
    """The kernel's call stubs get their names (syscalls.py), once per project: at import, and for projects imported
    before this existed, when the app starts. Returns how many were named."""
    from . import syscalls

    proj = net.project_row(slug)
    cfg = net.cfg(proj)
    if cfg.get("kind") != "rom" or cfg.get("kernel_calls_named") or net._link(slug):
        return 0  # (a room's guest: the host names them)
    rows = net.store.all("SELECT name, target FROM functions WHERE project_id=? ORDER BY vram", proj["id"])
    pairs = syscalls.stub_names([(r["name"], r["target"]) for r in rows], set(net.symbols(proj["id"]).by_name))
    if pairs:
        agent, _ = net.local_agent("autopilot")
        said = net.rename_symbols(agent, slug, pairs, f"name the {len(pairs)} kernel calls (syscall stubs)")
        named = said.startswith("✅")
        log(f"named {len(pairs)} kernel calls (FlushCache, CreateThread, ...)" if named
            else f"the kernel calls keep their names: {said}")
    # once either way: a check that fails (it compiles every matched function again) would fail at every start
    net._update_cfg(slug, lambda c: c.__setitem__("kernel_calls_named", True))
    return len(pairs) if not pairs or named else 0


PS2 = PlayStation2()
