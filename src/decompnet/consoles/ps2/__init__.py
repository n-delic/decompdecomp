"""PlayStation 2: the Emotion Engine's MIPS (R5900, little-endian), Sony's EE GCC and CodeWarrior compilers, the
kernel's calls, ROM mode.

- elf.py: the game's program (an ELF, e.g. SLUS_200.00) and which compiler its code looks built with.
- disc.py, chd.py: the disc image the program comes on (ISO 9660 as .iso or raw .bin, or a CHD), read in place.
- cso.py: CSO images (a disc deflated block by block), read in place, also for the PSP's and the PS1's discs.
- overlays.py: code the game keeps in other files of its disc, placed at the address it's loaded at.
- syscalls.py: names for the kernel's call stubs (FlushCache, CreateThread...), from the number each one loads.
- symtab.py, mdebug.py, stabs.py, dwarf1.py, cheader.py, debuginfo.py: a game's own debug symbols, when it shipped with them
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
OBJECT_READING = 1  # how elf.object_compilers reads a program: a higher one reads every project's program again


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
        from . import chd, cso, disc, elf

        return elf.is_mips_elf(head) or chd.is_chd(head) or cso.is_cso(head) or disc.is_disc(head)

    def read_game(self, file, name: str = "") -> Picked:
        """The program: an ELF the user picked, or the one a disc image names (with the disc's overlays)."""
        from . import chd, cso, disc, elf

        if isinstance(file, (str, Path)):
            path = Path(file)
            name = name or path.name
            with open(path, "rb") as f:
                head = f.read(8)
            if chd.is_chd(head) or cso.is_cso(head):  # a compressed disc image: read in place, a piece at a time
                with chd.Chd(path) if chd.is_chd(head) else cso.Cso(path) as image:
                    if disc.is_disc(image):
                        return self._from_disc(image)
                raise RomError(f"this {'CHD' if chd.is_chd(head) else 'CSO'} doesn't hold a PlayStation 2 disc this "
                               "app can read")
            if path.stat().st_size <= SMALL:
                return self.read_game(path.read_bytes(), name)
            with open(path, "rb") as f:
                if disc.is_disc(f):
                    return self._from_disc(f)
            raise RomError("this file is too big for a program and isn't a disc image this app can read")
        if elf.is_mips_elf(file):
            program, info = elf.read_elf(bytes(file), name)
            return Picked(self.id, program, "game.elf", info, False)
        if cso.is_cso(bytes(file[:8])):
            with cso.Cso(file) as image:
                return self._from_disc(image)
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
        """Unless the user picked a compiler: the object files another compiler built than most of the game
        (elf.object_compilers: CodeWarrior's C library in a GCC game) get it as their range, then the installed
        compilers are tried on the rest. What a program says about its compiler can mislead (a PS2 game with 12,000
        functions keeps one library's "MW MIPS C Compiler" note; its own code is EE-GCC's); the judge doesn't."""
        if not chosen:
            cfg = net.cfg(net.project_row(slug))
            by_guess = {c.guess: c for c in self.compilers}
            parts = [p for p in cfg["rom"].get("compiler_parts", []) if p[2] != cfg["rom"]["compiler_guess"]]
            for guess in sorted({p[2] for p in parts}):
                spans = [[start, end] for start, end, g in parts if g == guess]
                net.set_compiler_ranges(slug, spans, by_guess[guess].id)
                log(f"{len(spans)} stretches of object files are {by_guess[guess].name}'s, by the "
                    f"program's symbols: they build with it")
            net.test_compilers(slug, [c.id for c in self.compilers], log=log)
        net._update_cfg(slug, lambda c: c.__setitem__("object_reading", OBJECT_READING))
        name_kernel_calls(net, slug, log=log)

    def on_start(self, net, slug: str, log=print) -> None:
        name_kernel_calls(net, slug, log=log)  # projects imported before their kernel calls were named at import
        self.suggest_object_compilers(net, slug)

    def suggest_object_compilers(self, net, slug: str) -> None:
        """A project imported before its program's object files were read (elf.object_compilers), or by an older
        reading: what they say, as the autopilot's suggestions. The project's compiler ranges are its agents' work,
        voted on and checked against their neighbours, so the symbols don't overwrite them: their ranges go through
        the same range check as an agent's, and another compiler for the whole game to the user."""
        from collections import Counter

        from ...project import compiler_for
        from . import elf

        proj = net.project_row(slug)
        cfg = net.cfg(proj)
        program = Path(proj["root"]) / cfg.get("rom", {}).get("program", "")
        if cfg.get("kind") != "rom" or cfg.get("object_reading", 0) >= OBJECT_READING or net._link(slug) \
                or not program.is_file():  # (a room's guest: the host reads it; the game's files gone: they may come back)
            return
        parts = elf.object_compilers(program.read_bytes())
        by_guess = {c.guess: c for c in self.compilers}
        family = {c.id: c.guess.split("-")[0] for c in self.compilers}
        built: Counter = Counter()
        for start, end, guess in parts:
            built[guess] += end - start
        said = []
        agent, _ = net.local_agent("autopilot")
        main = built.most_common(1)[0][0] if parts else None
        main_agrees = main is None or family.get(cfg["compiler"]["toolchain"]) == main.split("-")[0]
        if not main_agrees:
            share = round(100 * built[main] / sum(built.values()))
            net.suggest(agent, slug, f"The program's symbols say {by_guess[main].name} built {share}% of the game's code "
                        "(object files with its mark): the project's compiler.", compiler=by_guess[main].id)
            said.append(f"{by_guess[main].name} for the game, suggested")
        for guess in sorted({g for *_, g in parts if g != main}):
            spans = []
            for start, end, g in parts:
                inside = net.store.all("SELECT vram FROM functions WHERE project_id=? AND vram>=? AND vram<?",
                                       proj["id"], start, end)
                if g == guess and inside and not (main_agrees and all(
                        family.get(compiler_for(cfg, r["vram"])["toolchain"]) == guess.split("-")[0] for r in inside)):
                    spans.append((start, end))
            if spans:
                net.suggest(agent, slug, f"The program's symbols say {by_guess[guess].name} built these object files.",
                            compiler=by_guess[guess].id, ranges=", ".join(f"0x{a:X}-0x{b:X}" for a, b in spans))
                said.append(f"{by_guess[guess].name} for {len(spans)} stretches of object files, range-checked")
        net._update_cfg(slug, lambda c: c.__setitem__("object_reading", OBJECT_READING))
        if said:
            with net.store.tx() as db:
                net._event(db, proj["id"], None, "analysis", "the program's symbols name its compilers: "
                           + "; ".join(said))

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
