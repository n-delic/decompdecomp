"""PlayStation 3: the Cell's PowerPC core (the PPU: 64-bit PowerPC, big-endian, ELF64, function descriptors and a TOC),
ROM mode, from the user's own decrypted `EBOOT.ELF`.

- elf.py: the reader: is it a PS3 program, its program headers (the code is the lowest segment with a payload), the
  module descriptor at `e_entry` (a retail program lists its functions there), the ABI, `.comment`'s compiler stamp,
  and the refusal of a retail SELF (decompdecomp never decrypts a game: the user does that with their own tools).
- split.py: the splitter (no tool reads a linked PowerPC64 program): descriptor functions, `bl` targets and
  prologues; assembly with calls and data named by address; the functions' references the judge compares with.
- imports.py + nids.py (nids.json, from PSL1GHT): the import tables a program carries, its import stubs and kernel calls
  named (`<library>_<function>`, `sys_...`) and the stubs set aside, at import and Check again (`name_system_calls`).
- symbols.py: the program's own symbol table, when it kept one (a function's symbol points at its descriptor).
- guides/ps3.md: what agents read about matching PS3 code.
- The judge is arch/ppc64.py's (`PpcTarget`, `link`): descriptors, the TOC and the linker's choices are judged by what
  the code refers to, then the game's bytes are compared. See its docstring for what a retail program must still
  confirm.

Its demo game is projects/demo-ps3 (Demo Quest built with the free ps3dev GCC; the free compiler makes the usual
64-bit ELFv1 program, where Sony's has 32-bit pointers and 8-byte descriptors: both are read).

The compilers retail games were built with come in the user's own SDK (kits/ps3sdk.py, "ps3sdk"): its GCC (and SNC)
are run from the user's copy, one compiler per SDK generation (`ps3sdk-270`); a game's `.comment` stamp ("SDK270")
picks the copy whose build equals it (default_compiler), else the free compiler is used and the import says which SDK
to set up. The free pack leaves out libgcc, the C runtime and Sony's intrinsics headers (si2vmx.h, vmx2spu.h,
spu2vmx.h, spu_intrinsics.h, spu_mfcio.h): the SDK copy provides them.

Not yet (ROADMAP.md): a real SDK's layout confirmed on one, the retail import stubs' exact shape (imports.py reads the
PSL1GHT shape: a stub that reaches its slot through the TOC isn't recognised), SPU programs and PRX modules, a rebuildable export, and C++ classes, struct layouts and the
compiler map.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...rom import Picked, RomError
from .. import BETA, ROM, Compiler, Console


class PlayStation3(Console):
    id = "ps3"
    name = "PlayStation 3"
    short = "PS3"
    status = BETA  # its demo builds and judges; no real game has been split and matched yet
    arch = "ppc64"
    big_endian = True
    mode = ROM
    layout = "descriptor + prologues"
    compilers = (
        Compiler("ps3dev-gcc", "PS3 GCC (ps3dev)", "homebrew and the demo game (the free PPU GCC; not the period "
                 "compiler retail games used)", guess="PS3DEV"),
    )
    kits = ("ps3sdk",)  # the user's own SDK: Sony's compilers, which the free pack can't give
    guides = ("ps3",)
    m2c = ""  # m2c has no 64-bit PowerPC target
    ram = (0x00010000, 0xD0000000)  # a program's code and data live in the first 256 MiB; the rest is video and I/O
    demo = "demo-ps3"
    demo_tools = ("ps3dev-gcc",)
    as_flags = ("-mppc64", "-be")
    image_ext = ".elf"
    asm_preamble = ".text\n"

    def sniff(self, head: bytes, file) -> bool:
        from . import elf

        return elf.looks_like_self(head) or elf.is_ps3_elf(head)

    def read_game(self, file, name: str = "") -> Picked:
        """The decrypted ELF to split; a SELF is refused with what to do about it. Its title and title ID come from
        the disc's PARAM.SFO when the ELF is in its folder (PS3_GAME/USRDIR/EBOOT.ELF beside PS3_GAME/PARAM.SFO)."""
        from . import elf

        known = {}
        if isinstance(file, (str, Path)):
            path = Path(file)
            name = name or path.name
            known = _title(path.parent.parent / "PARAM.SFO")
            file = path.read_bytes()
        program, info = elf.read_elf(bytes(file), name)
        return Picked(self.id, program, "game.elf", info | known, True)

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        """The copy of the user's SDK whose compiler built the game (its .comment stamp, "SDK270", against each copy's
        build); without one, the free compiler, and the reason says which SDK would build it exactly."""
        from ... import kits

        sdk = info.get("sdk", "")
        if not sdk:
            return super().default_compiler(info, splat_guess)
        own = [c for c in kits.compilers(self.kits) if c.build == sdk]
        if own:
            info["compiler_why"] = f"the game's compiler stamp is {sdk}: your SDK copy's {own[0].name} built it"
            return own[0].version
        info["compiler_why"] = (f"the game's compiler stamp is {sdk} (Sony's GCC): set up your own PlayStation 3 SDK "
                                f"of that generation in Setup, under Compilers & tools, to build with it; until then "
                                f"{self.fixed_compilers[0].name} is used, which won't match Sony's code exactly")
        return super().default_compiler({**info, "compiler_guess": ""}, splat_guess)

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """Sony's PPU GCC aligns functions to 4 bytes (-falign-functions=4); pointers are 32 bits by the compiler's
        default (-mlp32), so no flag asks for it. No $gp: the PS3 has no small data."""
        flags = super().default_flags(toolchain, gp)
        return flags + [f for f in ("-falign-functions=4",) if f not in flags]

    # GCC's -mminimal-toc: a file's globals reached through a table of its own, its address kept in r30 (a retail game
    # built with it: `std r30,-16(r1); lwz r30,N(r2); lwz r9,-32768(r30)`); the import reads it off the code
    flag_extras = ("-mminimal-toc",)

    def game_flags(self, cfg: dict, toolchain: str) -> list[str]:
        """-mminimal-toc for a game the import saw built with it (elf.minimal_toc), on a GCC: SN's SNC has no such flag."""
        return ["-mminimal-toc"] if (cfg.get("rom") or {}).get("minimal_toc") and "snc" not in toolchain else []

    def split_game(self, workdir: Path, picked: Picked, *, symbols: list[str] | None = None, log=print):
        from . import split

        return split.split_result(picked.data, log=log, symbols=symbols)

    def debug_info(self, program: bytes, known: set[str]):
        """The game's own names, when it kept its symbol table (symbols.py): its functions and data are named by them,
        as the PS2's are. Types and prototypes (DWARF) aren't read yet."""
        from ..ps2 import debuginfo
        from . import symbols

        named = symbols.read(program)
        return debuginfo.DebugInfo(named=named) if named else None

    def function_insns(self, fn) -> int:
        return fn.n_insns if hasattr(fn, "n_insns") else fn.get("n_insns") or 1

    def function_asm(self, fn) -> str:
        return fn.asm if hasattr(fn, "asm") else fn["asm"]

    def function_meta(self, fn) -> dict:
        """What a function's row keeps besides its bytes: the pointer width and TOC base its references were read with."""
        from ...arch.ppc64 import slots_of

        return {"ptr": fn.ptr, "toc": fn.toc, "slots": slots_of(fn.refs)}

    def function_from_row(self, row):
        from .split import function_from_row

        return function_from_row(row)

    def parse_function(self, name: str, text: str, segment: str):
        from .split import parse_function

        return parse_function(name, text, segment)

    def split_function(self, fn, at: int, new_name: str):
        from .split import split_function

        return split_function(fn, at, new_name)

    def function_at(self, rom: bytes, addr: int, limit: int, name: str):
        from .split import function_at

        return function_at(rom, addr, limit, name)

    def join_saved_functions(self, rows: list[dict], start: int, end: int, read):
        from .split import join_saved_functions

        return join_saved_functions(rows, start, end, read)

    def target_from_split(self, fn, resolve, big_endian: bool, gp: int):
        from ...arch.ppc64 import PpcTarget

        return PpcTarget(fn.name, fn.vram, fn.code, fn.refs, fn.ptr, fn.toc)

    def target_from_row(self, fn: dict, cfg: dict):
        from ...arch.ppc64 import PpcTarget

        meta = json.loads(fn["meta"] or "{}")
        return PpcTarget(fn["name"], fn["vram"], fn["target"], PpcTarget.refs_from_json(json.loads(fn["relocs"] or "[]"), meta.get("slots")),
                         meta.get("ptr", 4), meta.get("toc", 0))

    def target_object(self, fn: dict, cfg: dict, symbols):
        from ...arch.ppc64 import canonical_target

        return canonical_target(self.target_from_row(fn, cfg))

    def link_function(self, obj: bytes, name: str, target, symbols, read):
        from ...arch import ppc64

        return ppc64.link(obj, name, target, symbols, read)

    def after_import(self, net, slug: str, *, chosen: bool, log=print) -> None:
        name_system_calls(net, slug, log=log)

    def on_start(self, net, slug: str, log=print) -> None:
        name_system_calls(net, slug, log=log)  # projects imported before their imports were named

    def export_project(self, net, slug: str, out: Path, log=print) -> dict:
        raise RomError("a PlayStation 3 project can't be exported as a rebuildable tree yet (ROADMAP.md): the matched "
                       "C is saved with the project")


def name_system_calls(net, slug: str, log=print) -> int:
    """The program's import stubs and kernel calls get their names (imports.py), once per project: at import, and for
    projects imported before this existed, when the app starts. An import stub is set aside too: the loader links it
    to a system function, so no C is behind it. Returns how many were named."""
    from . import imports

    proj = net.project_row(slug)
    cfg = net.cfg(proj)
    program = Path(proj["root"]) / cfg.get("rom", {}).get("program", "")
    if cfg.get("kind") != "rom" or cfg.get("system_calls_named") or net._link(slug) or not program.is_file():
        return 0  # (a room's guest: the host names them; the game's files gone: they may come back)
    rows = net.store.all("SELECT id, name, target, meta, status FROM functions WHERE project_id=? ORDER BY vram", proj["id"])
    pairs, stubs = imports.names([(r["name"], r["target"]) for r in rows], imports.read_imports(program.read_bytes()),
                                 set(net.symbols(proj["id"]).by_name))
    why = "an imported system function's stub: the loader links it to the library function its NID names, so no C is behind it"
    with net.store.tx() as db:
        db.executemany("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'",
                       [(json.dumps({**json.loads(r["meta"] or "{}"), "asm_code": why}), r["id"])
                        for r in rows if r["name"] in stubs])
    named = False
    if pairs:
        agent, _ = net.local_agent("autopilot")
        said = net.rename_symbols(agent, slug, pairs, f"name the {len(pairs)} import stubs and kernel calls")
        named = said.startswith("✅")
        log(f"named {len(pairs)} imported functions and kernel calls (sys_process_getpid, ...)" if named
            else f"the imports keep their names: {said}")
    # once either way: a check that fails (it compiles every matched function again) would fail at every start
    net._update_cfg(slug, lambda c: c.__setitem__("system_calls_named", True))
    return len(pairs) if named else 0


PS3 = PlayStation3()


def _title(path: Path) -> dict:
    """The title and title ID of a PS3 disc's PARAM.SFO (the PSP's format: consoles/psp/sfo.py), or {}."""
    from ...rom import RomError
    from ..psp import sfo

    try:
        keys = sfo.read(path.read_bytes())
    except (OSError, RomError):
        return {}
    out = {k: v for k, v in (("title", keys.get("TITLE")), ("game_code", keys.get("TITLE_ID"))) if isinstance(v, str) and v}
    # a title ID's third letter is its region: BCUS, BLES, NPJB...
    region = REGIONS.get(out.get("game_code", "")[2:3])
    return out | ({"region": region} if region else {})


REGIONS = {"U": "North America", "E": "Europe", "J": "Japan", "A": "Asia", "K": "Korea", "H": "Asia"}
