"""The original Xbox: 32-bit x86 programs (XBE) built with Visual C++ 7.1 (or 7.0) from Microsoft's Xbox Development
Kit, PROJECT mode. An Xbox game is a Windows-style program in a format of its own, so it goes the PC console's way
(consoles/pc/), all of it: the import makes the XBE a Windows program at the same addresses (scratch.pe_view, in the
project's orig/), and from there Ghidra finds the functions, each is cut out into a COFF object objdiff judges what
src/ compiles to against, the whole program is rebuilt from the project and checked, and every PC feature (names,
joins and cuts, split files, per-file compilers and flags, classes, struct layouts, drafts) works on it as it is.
What's the Xbox's is reading the game: the XBE, its disc image, its kernel imports, its XDK library sections.

- scratch.py: reads an XBE (or default.xbe on its disc image) and makes its PE view; the compiler comes from the
  XDK build its library version table names; the XDK's library sections (D3D, DSOUND, XNET...) are set aside.
- xboxkrnl.py: the kernel's exports by ordinal (nxdk's CC0 list), so `call [kernel thunk slot]` is `__imp__NtClose`.
- xdvdfs.py: files out of an Xbox disc image (XISO, or a full Redump dump), reading only their sectors.
- guides/xbox.md: what agents read about Xbox games, beyond the x86 guide.

Games built with link-time code generation (/GL, /LTCG: the linker gives functions register conventions of its own and
inlines across files) are told at import (forks/ltcg.py, forks/convention.py), and their units build in harness mode
(the PC template's tools/ltcg.py: Visual C++'s linker, through wine).

The user's own XDK is a kit (kits/xdk.py, Console.kits): with it, the XDK's library code linked into .text (the C
runtime, XAPI) is found by its libraries' bytes and set aside (xdklib.py), and a game of an early XDK builds with
that XDK's own Visual C++ (MSVC/xdk-3911, one per XDK copy). `ninja rebuild` writes the game's default.xbe back out of the PE view.

"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from ...network import NetworkError
from .. import BETA, Compiler
from ..pc import WindowsPC

GAME_STEP = ("`orig/{version}.exe` is your copy of the game's default.xbe made a Windows program at the same addresses: "
             "decompdecomp makes it from your default.xbe or disc image when you open the project")
WHAT = "the PE view of your copy of the game's program (default.xbe), which decompdecomp makes from it"


class Xbox(WindowsPC):
    id = "xbox"
    name = "Xbox"
    short = "Xbox"
    aliases = ()
    status = BETA
    compilers = (Compiler("MSVC/7.1", "Visual C++ 7.1", "games made with the XDK from 2003 on"),
                 Compiler("MSVC/7.0", "Visual C++ 7.0", "games made with earlier XDKs"))  # (+ your XDKs': kit_compilers)
    packs = (("msvc", "Visual C++ 7.0 and 7.1, the compilers of Xbox games (proprietary)"),
             ("ghidra", "finds an Xbox game's functions, and drafts of its code (540 MB, needs Java 21+)"))
    kits = ("xdk",)  # the user's own XDK: its libraries' code set aside, its compiler for early games
    guides = ("x86", "xbox", "cpp")
    ram = (0x00010000, 0x80000000)  # an XBE loads at 0x10000; the kernel is above 0x80000000
    demo = "demo-xbox"
    import_options = (("ltcg", "Link-time code generation (/GL, /LTCG)", (
        ("auto", "Detect it from the program (recommended)"),
        ("on", "The game was built with it: build units through a harness"),
        ("off", "The game wasn't: build every unit on its own"))),)

    def sniff(self, head: bytes, file) -> bool:
        from . import scratch, xdvdfs

        return scratch.is_xbe(head[:scratch.HEAD_BYTES]) or xdvdfs.is_xbox_disc(file)

    def upload_name(self, data: bytes) -> str:
        from . import scratch

        return "default.xbe" if scratch.is_xbe(bytes(data[:scratch.HEAD_BYTES])) else "game.iso"

    def build_env(self) -> dict[str, str]:
        """Wine for the builds of games with link-time code generation, whose units link with Visual C++'s linker
        (the PC template's tools/ltcg.py): wherever wine is, though no pack of the Xbox's needs it to compile."""
        from ... import wine

        return wine.build_env(always=True)

    def program_in(self, folder: Path) -> Path | None:
        return None  # an Xbox game is its default.xbe or its disc image, never a folder of DLLs

    def tool_args(self) -> list[str]:
        """The PC's, and the compilers of the kits the user set up (each XDK's own Visual C++: MSVC/xdk-3911)."""
        from ..pc import scratch as pc

        return pc.tool_args(self.kits)

    def game_info(self, game: Path) -> dict:
        from . import scratch

        return scratch.game_info(game)

    def identity(self, path: Path) -> str | None:
        """The PE view's SHA-1: a disc image and the default.xbe out of it are the same game."""
        from . import scratch

        return hashlib.sha1(scratch.pe_view(scratch.program_bytes(path))).hexdigest()

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, ltcg: bool | None = None,
                       log=print) -> dict:
        """The PC path's project, from the XBE's PE view, then the XDK's library sections set aside. `ltcg`: whether
        the game was built with link-time code generation (None, or "auto": told from the program, forks/ltcg.py;
        "on" and "off" as the import's choice says)."""
        ltcg = {"on": True, "off": False, "auto": None}.get(ltcg, ltcg)
        from ...forks import ltcg as ltcg_mode
        from ..pc import scratch as pc
        from . import scratch

        info = scratch.game_info(game)
        version = (compiler or info["compiler"]).removeprefix("MSVC/")
        with tempfile.TemporaryDirectory(prefix="decomp-xbe-") as tmp:
            view = Path(tmp) / f"{info['id']}.exe"  # (alone in its folder: find_modules looks beside it for DLLs)
            view.write_bytes(scratch.pe_view(scratch.program_bytes(game)))
            def plan(pe, functions) -> dict:  # (the XDK's library sections are set aside below: not looked at)
                library = {f.addr for f in functions if (s := pe.section_at(f.addr)) is not None
                           and s.name in info["library_sections"]}
                chosen = ltcg_mode.plan(pe, functions, library, scratch.CL_FLAGS, ltcg, log)
                return {**chosen, "cl_flags": scratch.cl_flags(version, chosen["cl_flags"])}

            made = pc.create(root, view, compiler=compiler, log=log, info=info, platform=self.id, what=WHAT,
                             cl_flags=scratch.cl_flags(version),
                             game_step=GAME_STEP.format(version=info["id"]), args=self.tool_args(), plan=plan)
        if said := scratch.set_aside_libraries(root, info):
            log(said)
        return made

    def prepare_fork_game(self, src: Path, dest: Path, sha1: str | None, *, move: bool = False) -> bool:
        """A saved or reimported project's program is the PE view: made again from the user's XBE or disc image."""
        from ...forks.project import ForkError
        from . import scratch

        if dest.suffix != ".exe" or not (scratch.is_xbe(_head(src)) or _is_disc(src)):
            return False
        view = scratch.pe_view(scratch.program_bytes(src))
        if sha1 and hashlib.sha1(view).hexdigest() != sha1:
            raise ForkError("that isn't this project's game (its program differs): choose the same default.xbe or "
                            "disc image")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(view)
        return True

    def reimport(self, net, slug: str, game: Path, log=print) -> str | None:
        from . import scratch

        tmp = Path(tempfile.mkdtemp(prefix="decomp-xbe-"))
        try:
            view = tmp / "game.exe"
            view.write_bytes(scratch.pe_view(scratch.program_bytes(game)))
            said = super().reimport(net, slug, view, log=log)
            info = scratch.game_info(game)  # (the new analysis's functions: the libraries' found again)
            more = net.forks.reanalyse(slug, lambda fp: scratch.set_aside_libraries(fp.root, info), log=log)
            return "; ".join(s for s in (said, more) if s) or None
        except OSError as e:
            raise NetworkError(f"couldn't read {game.name}: {e}") from None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _head(path: Path) -> bytes:
    from . import scratch

    with open(path, "rb") as f:
        return f.read(scratch.HEAD_BYTES)


def _is_disc(path: Path) -> bool:
    from . import xdvdfs

    return xdvdfs.is_xbox_disc(path)


XBOX = Xbox()
