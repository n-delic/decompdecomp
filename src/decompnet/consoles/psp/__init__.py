"""PlayStation Portable: the Allegrex (a little-endian MIPS II with the PSP's own instructions and its VFPU vector
unit), Sony's SDK GCC, CodeWarrior and SN Systems' SNC compilers, ROM mode.

- prx.py: the game's program, an ELF for the Allegrex: a relocatable module (PRX) is laid out at the address the
  loader gives a game's main module and becomes a fixed program; its $gp, its imported functions' names, and which
  compiler its note says.
- nids.py, nids.json: the system functions' names by library and NID, from the open-source PSP SDK (LICENSE.pspsdk);
  `python -m decompnet.consoles.psp.nids` remakes them.
- modules.py: the game's other modules (the PRX files beside its program, loaded later), each laid out after the main
  program and split as a segment of its own.
- sfo.py: PARAM.SFO, the disc's facts about its game (title, product code).
- the disc itself (an ISO 9660 UMD image, or a CSO or CHD of one) is read in place with the PS2's readers
  (consoles/ps2/disc.py, cso.py, chd.py).
- guides/psp.md: what agents read about matching PSP code.

Its demo game is projects/demo-psp (the Demo Quest game built with the SDK's GCC 1.3.1 at -O2 -G8, at the address a
main module is loaded at; `build.py pspsnc-1.2.7503` builds it with SN's SNC).

Not yet (ROADMAP.md): programs and modules still encrypted (most retail EBOOT.BINs: the user decrypts them first),
and ZSO images (LZ4: decompressed first).
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

from ...arch import mips
from ...rom import Picked, RomError
from .. import BETA, EXPORT, ROM, Compiler, Console

SMALL = 64 << 20  # programs are read whole; disc images are read from disk
SMALL_DATA_BYTES = 8  # globals this small sit in $gp-relative small data when a game uses $gp
ELF_ENTRY = slice(24, 28)  # e_entry in a 32-bit ELF header
ENCRYPTED = b"~PSP"  # a program Sony encrypted: the PSP decrypts it as it loads it
PARAM_SFO = "PSP_GAME/PARAM.SFO"
PROGRAMS = ("PSP_GAME/SYSDIR/BOOT.BIN", "PSP_GAME/SYSDIR/EBOOT.BIN")  # a plain one first: BOOT.BIN is often zeros
CODEWARRIOR_BUILDS = (219, 210, 205, 201, 192, 180, 151, 147, 139, 134, 121)  # the newest first: the default


class PlayStationPortable(Console):
    id = "psp"  # decomp.dev's id, which public PSP decomps keep
    name = "PlayStation Portable"
    short = "PSP"
    status = BETA  # its demo matches and rebuilds; no real game has been rebuilt byte for byte whole yet
    arch = "mips"
    cpu_variant = mips.ALLEGREX
    big_endian = False
    mode = ROM
    layout = "splat"
    compilers = (
        Compiler("psp-gcc-1.7.1", "PSP GCC 3.3.3 (SDK 1.7.1)", "PSP games built with the later SDKs' GCC (2005 on)",
                 guess="PSPGCC-1.7"),
        Compiler("psp-gcc-1.3.1", "PSP GCC 3.3.3 (SDK 1.3.1)", "launch-era PSP games built with the SDK's GCC "
                 "(2004-2005)", guess="PSPGCC-1.3"),
        Compiler("pspsnc-1.2.7503", "SNC 1.2 (SN Systems)", "PSP games built with SN Systems' ProDG (proprietary)",
                 guess="SNC"),
        *(Compiler(f"mwccpsp-3.0.1-b{build}", f"CodeWarrior PSP 3.0.1 b{build}",
                   "PSP games built with CodeWarrior (2004-2006, proprietary)",
                   guess="MWCCPSP" if build == CODEWARRIOR_BUILDS[0] else "")
          for build in CODEWARRIOR_BUILDS),
    )
    guides = ("mips", "psp")
    compiler_guide = "psp"
    # m2c has no Allegrex target: the EE's reads its code, with the same EABI (arguments in a0-a3 and t0-t3) and
    # splat's numbered registers ($31), which its little-endian MIPS GCC target takes for a jump table's register
    m2c = "mipsee-gcc"
    # user memory, from 0x08800000 (the later models' extra memory reaches 0x0C000000), and past it the game's other
    # modules, each laid out after the last (modules.py), up to the end of the 256 MB the main program's jumps reach
    ram = (0x08800000, 0x10000000)
    demo = "demo-psp"
    # (1.7.1: the compiler a program without a note gets, as the demo is; binutils-mips: the export's)
    demo_tools = ("psp-gcc-1.3.1", "psp-gcc-1.7.1", "binutils-mips")
    own_features = frozenset({EXPORT})
    splat_platform = "psp"
    as_flags = ("-EL", "-march=mips2", "-mabi=eabi")  # GNU as knows no Allegrex: its own instructions are words

    def sniff(self, head: bytes, file) -> bool:
        from ..ps2 import cso
        from . import prx

        # (a ZSO, which can't be read yet, is taken too: read_game says to decompress it)
        zso = head[:4] == cso.ZSO_MAGIC and cso.is_cso(head)
        return prx.is_psp_elf(head) or head[:4] == ENCRYPTED or zso or is_psp_disc(file)

    def read_game(self, file, name: str = "") -> Picked:
        """The program: an ELF or PRX the user picked, or the one a UMD image has."""
        from ..ps1.exe import opened

        if isinstance(file, (str, Path)):
            path = Path(file)
            name = name or path.name
            with open(path, "rb") as f:
                head = f.read(4)
            if head in (b"\x7fELF", ENCRYPTED) and path.stat().st_size <= SMALL:
                return self._from_program(path.read_bytes(), name)
            with opened(path) as src:
                return self._from_disc(src)
        if bytes(file[:4]) in (b"\x7fELF", ENCRYPTED):
            return self._from_program(bytes(file), name)
        with opened(file) as src:  # (a CSO's bytes: inflated a block at a time)
            return self._from_disc(src)

    def _from_disc(self, src) -> Picked:
        from ..ps2 import disc
        from . import prx, sfo

        image = disc._Disc(src)
        param = image.find(PARAM_SFO)
        if param is None:
            raise RomError("this disc image isn't a PSP game: it has no PSP_GAME/PARAM.SFO")
        facts = sfo.read(param)
        encrypted = None
        for where in PROGRAMS:
            data = image.find(where)
            if data and prx.is_elf(data):
                return self._with_modules(image, self._from_program(data, where.rsplit("/", 1)[-1], facts))
            if data and data[:4] == ENCRYPTED:
                encrypted = where
        if encrypted:
            raise RomError(_encrypted(encrypted))
        raise RomError("this PSP disc's program (PSP_GAME/SYSDIR/EBOOT.BIN) isn't one this app can read")

    def _with_modules(self, image, picked: Picked) -> Picked:
        """The disc's other modules with the main program: each a segment of its own after it (modules.py), their
        import stubs set aside with its, their own names read with its (Picked.programs)."""
        from . import modules, prx

        try:
            found, left_out = modules.find(image, picked.data)
        except (RomError, struct.error, IndexError):  # an odd disc: the program alone still works
            return picked
        picked.overlays = modules.overlays(found)
        picked.programs = [m.program for m in found]
        picked.info["stubs"] += [list(r) for m in found if (r := prx.stub_range(m.program))]
        picked.info["modules_left_out"] = left_out
        # modules that keep small data where the main program doesn't, or the other way round: after_import gives
        # them their own flags
        picked.info["small_data_differs"] = [[vaddr, vaddr + len(data)] for m in found
                                             if m.small_data != picked.info["small_data"]
                                             for vaddr, data, _ in modules.loaded(m.program)]
        return picked

    def _from_program(self, data: bytes, name: str, facts: dict | None = None) -> Picked:
        from . import prx, sfo

        if data[:4] == ENCRYPTED:
            raise RomError(_encrypted(name or "this program"))
        if not prx.is_psp_elf(data):
            raise RomError("this isn't a PSP program (an ELF or PRX for the PSP's Allegrex) or UMD image")
        program = prx.relocate(data)
        facts = facts or {}
        code = sfo.product_code(str(facts.get("DISC_ID", "")))
        title = str(facts.get("TITLE", "")).strip()
        if title and code and not any(c.isascii() and c.isalnum() for c in title):
            title = f"{title} ({code})"  # a title in Japanese alone: the project's name needs letters it can spell
        return Picked(self.id, program, "game.elf", {
            "title": title or code or name or "PSP game", "game_code": code,
            "region": sfo.region_of(code) if code else "Unknown region",
            "version": 0, "entrypoint": struct.unpack("<I", program[ELF_ENTRY])[0], "gp": prx.gp_value(program),
            "crc1": 0, "crc2": 0, "size": len(data), "sha1": hashlib.sha1(data).hexdigest(), "byte_order": "elf",
            "compiler_guess": prx.guess_compiler(program), "platform": self.id, "boot": name,
            "small_data": prx.uses_small_data(data),  # (every module has a gp value: whether its code uses it)
            "prx": program is not data,  # laid out at prx.USER_BASE, as the loader would
            # after_import sets the import stubs aside: the program's, then its modules' (_with_modules)
            "stubs": [list(r) for r in [prx.stub_range(program)] if r],
        }, False)

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """A game that keeps data $gp-relative (prx.uses_small_data: not every module that has a gp value does)
        keeps globals up to 8 bytes there (GCC's and SNC's -G8; CodeWarrior's -sdatathreshold 8). SNC's own default
        is -G0, as GCC's is."""
        flags = super().default_flags(toolchain, gp)
        small = str(SMALL_DATA_BYTES if gp else 0)
        if toolchain.startswith("mwccpsp"):
            return [*flags, "-sdatathreshold", small]
        if toolchain.startswith(("psp-gcc", "pspsnc")):
            return [f for f in flags if not f.startswith("-G")] + [f"-G{small}"]
        return flags

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        """splat's config, as its PS2 reader wrote it (rom/split.py), made the PSP's: the module's tables
        (.lib.stub, .rodata.sceModuleInfo, ...) are read-only data, which it files as .rodata, a section its order
        then lacks (PS2 programs always have one), and the compiler its disassembly allows for is the game's, not
        EE-GCC (whose branch labels it aligns). The disc's other modules follow as segments of their own, as a
        PS2 disc's overlays do (modules.py)."""
        from ..ps2.overlays import add_to_config
        from . import modules

        text = config.read_text(encoding="utf-8")
        if not re.search(r"^    - \.rodata$", text, re.M):
            text = re.sub(r"^(  section_order:\n    - \.text\n)", r"\g<1>    - .rodata\n", text, count=1, flags=re.M)
        splat_compiler = "MWCCPS2" if picked.info.get("compiler_guess") == "MWCCPSP" else "GCC"
        text = re.sub(r"^(  compiler: )\w+", rf"\g<1>{splat_compiler}", text, count=1, flags=re.M)
        config.write_text(text, encoding="utf-8")
        for line in modules.left_out_lines(picked.info.get("modules_left_out") or []):
            log(line)
        return add_to_config(config, workdir, image, picked.overlays, log) if picked.overlays else image

    def find_gp(self, image: bytes, program: bytes, segments: list) -> int:
        """The module info's gp_value (the loader sets $gp from it), else what the start-up code sets it to."""
        from . import prx

        return prx.gp_value(program) or mips.gp_from_entry(image, int.from_bytes(program[ELF_ENTRY], "little"),
                                                             segments)

    def debug_info(self, program: bytes, known: set[str]):
        """The game's own symbols and debug information, read as the PS2's are, and the imported functions' stubs
        named (prx.stub_names) where no function symbol names them. A stub's name the game already gives something
        else, or a second stub of one function (two of a library's stub tables can import it), gets _2, as the PS2's
        kernel stubs do."""
        from ..ps2 import debuginfo, symtab
        from . import prx

        info = debuginfo.read(program, known)
        stubs = prx.stub_names(program)
        if stubs:
            info = info or debuginfo.DebugInfo()
            addresses, used = {n.address for n in info.named}, {n.name for n in info.named}
            named = []
            for at, name in stubs.items():
                if at in addresses:
                    continue
                new, copy = name, 2
                while new in used:
                    new, copy = f"{name}_{copy}", copy + 1
                used.add(new)
                named.append(symtab.Named(new, at, prx.STUB_BYTES, True, prx.STUB_SECTION))
            info.named = sorted([*info.named, *named], key=lambda n: n.address)
        return info

    def debug_brief(self, known: dict) -> list[str]:
        from ..ps2 import debuginfo

        return debuginfo.brief_lines(known)

    def after_import(self, net, slug: str, *, chosen: bool, log=print) -> None:
        """The imported functions' stubs are set aside (the loader links each to a system function: no C is behind
        them), then, unless the user picked a compiler, the installed ones are tried on the game's code: what its
        note says is only a first guess (CodeWarrior's builds all write the same note)."""
        set_stubs_aside(net, slug, log=log)
        if not chosen:
            net.test_compilers(slug, [c.id for c in self.compilers], log=log)
            set_module_small_data(net, slug, log=log)

    def export_asm(self, text: str) -> str:
        """The Allegrex's own instructions (and the VFPU's) as the words they are: GNU as reads MIPS II, not them.
        A word is the same bytes wherever the export puts it, as it puts everything at the game's addresses. 8-byte
        data too, as every console's (mips.raw_doubles)."""
        def one(m: re.Match) -> str:
            word = int.from_bytes(bytes.fromhex(m[2]), "little")
            return f"{m[1]}.word 0x{word:08X}" if allegrex_only(word) else m[0]
        return mips.raw_doubles(_ASM_LINE.sub(one, text), False)

    def asm_reason(self, code: bytes) -> str | None:
        return mips.asm_reason(code, False, eabi=True, cpu=self.cpu_variant)  # EABI: arguments in t0-t3 too


# a spimdisasm instruction line: `/* rom vram bytes */  mnemonic operands`, the bytes in the file's order
_ASM_LINE = re.compile(r"^(\s*/\* [0-9A-Fa-f]+ [0-9A-Fa-f]+ ([0-9A-Fa-f]{8}) \*/\s+)\S.*$", re.M)
SPECIAL2, CACHE = 0x1C, 0x2F  # SPECIAL2: MIPS32's, and the Allegrex's halt, mfic, mtic; cache: MIPS III's
# SPECIAL's functions MIPS II lacks: movz/movn (MIPS IV), and the Allegrex's clz/clo, madd/maddu, max/min,
# msub/msubu (where MIPS32 has other encodings, or none)
ALLEGREX_FUNCTIONS = {0x0A, 0x0B, 0x16, 0x17, 0x1C, 0x1D, 0x2C, 0x2D, 0x2E, 0x2F}
SRL, SRLV = 0x02, 0x06  # with a 1 where MIPS II has 0 (rs; the shift amount), rotr and rotrv


def allegrex_only(word: int) -> bool:
    """Whether an instruction is the Allegrex's (or the VFPU's) and not MIPS II's."""
    op, funct = word >> mips.OPCODE_SHIFT, word & mips.FUNCT_BITS
    if op in mips.VFPU_OPCODES or op in (SPECIAL2, mips.SPECIAL3, CACHE):
        return True
    if op != mips.SPECIAL:
        return False
    rotate = funct == SRL and (word >> mips.RS_SHIFT) & mips.REGISTER_BITS or \
        funct == SRLV and (word >> 6) & mips.REGISTER_BITS
    return funct in ALLEGREX_FUNCTIONS or bool(rotate)


def _encrypted(where: str) -> str:
    return (f"this game's program ({where}) is encrypted, as most PSP games' are, and decompdecomp can't decrypt it "
            "yet. Decrypt it first (PPSSPP, for one, can save a decrypted copy of a game's program as it starts the "
            "game: a setting among its developer tools), then import that decrypted ELF or PRX file")


def is_psp_disc(file) -> bool:
    """An ISO 9660 image (or a CSO or CHD of one) with the PSP's PSP_GAME/PARAM.SFO: other PlayStation discs are
    ISO 9660 too."""
    from ..ps1.exe import opened
    from ..ps2 import disc

    try:
        with opened(file) as src:
            return disc.is_disc(src) and disc._Disc(src).find(PARAM_SFO) is not None
    except (RomError, OSError, struct.error, IndexError):
        return False


def set_stubs_aside(net, slug: str, log=print) -> int:
    """The functions in the program's and its modules' import stubs (.sceStub.text) set aside: each is `jr ra; nop`
    until the loader makes it a jump to the system function it imports, so no C is behind it. Returns how many."""
    proj = net.project_row(slug)
    stubs = net.cfg(proj).get("rom", {}).get("stubs") or []
    ranges = [stubs] if stubs and isinstance(stubs[0], int) else stubs  # (one range: imported before modules were)
    why = ("an imported system function's stub: the PSP's module loader links it to the library function its NID "
           "names, so no C is behind it")
    rows = [r for start, end in ranges for r in net.store.all(
        "SELECT id, meta FROM functions WHERE project_id=? AND vram>=? AND vram<? AND status='open'",
        proj["id"], start, end)]
    with net.store.tx() as db:
        db.executemany("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'",
                       [(json.dumps({**json.loads(r["meta"] or "{}"), "asm_code": why}), r["id"]) for r in rows])
    if rows:
        log(f"{len(rows)} imported system functions' stubs set aside (no C is behind them)")
    return len(rows)


def set_module_small_data(net, slug: str, log=print) -> None:
    """The modules whose small data isn't the main program's (prx.uses_small_data: one may keep it, another not) as
    compiler ranges of the project's compiler with their own small data, so their functions build as they were built.
    After the compiler test, which may change the project's compiler. shortcut: a module that keeps small data is
    judged with the main program's $gp, not its own, until a game whose modules differ shows that it matters."""
    cfg = net.cfg(net.project_row(slug))
    spans = cfg["rom"].get("small_data_differs") or []
    if not spans:
        return
    toolchain = cfg["compiler"]["toolchain"]
    own = PSP.default_flags(toolchain, 0 if cfg["rom"].get("small_data") else cfg.get("gp") or 0)
    net.set_compiler_ranges(slug, spans, toolchain, own)
    log(f"{len(spans)} module segments keep small data {'off' if cfg['rom'].get('small_data') else 'on'}, unlike "
        "the main program: they build with their own flags")


PSP = PlayStationPortable()
