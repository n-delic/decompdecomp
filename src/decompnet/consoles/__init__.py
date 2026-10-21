"""The connector: how decompdecomp talks to a console. Every console is a package in this folder (consoles/n64/,
consoles/ps1/, consoles/ps2/, consoles/gc_wii/, consoles/pc/, consoles/nds/) whose `__init__.py` makes a `Console`
below and fills in its parts; the list at the end of this file registers them. The rest of the app (the engine, briefs, twins, the agents' tools
and prompts, the export, the GUI's API) asks the console of a project, `consoles.get(platform)`, and never compares
console names. A person or an agent working on PS2 support reads consoles/ps2/ and this file, nothing else.

A console works in one of two modes:
- ROM: decompdecomp splits the game itself (splat, rom/split.py), and the judge links each compiled candidate at the
  function's real address and compares the bytes (rom/match.py). The console reads its file (`read_game`), names its
  compilers and flags, and may add code splat's own look misses (`add_code`). N64, PS1 and PS2.
- PROJECT: decompdecomp generates the community's kind of project for the game (a git tree with an objdiff.json),
  and objdiff judges each function in the project's own build (forks/, app/forkengine.py). The console makes the
  project (`create_project`) and points its build at the pinned tools (`tool_args`). GameCube, Wii, PC and DS.

What's the processor's and not the console's (relocations, disassembly, hand-written assembly, the readers for the
compiler map and C++ classes) is in arch/. Adding a console: copy consoles/template/, fill in its parts, add a demo
game in projects/, add it to CONSOLES below, and make tests/test_consoles.py pass (CONTRIBUTING.md has the steps).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import arch, toolchains
from ..rom import Picked, RomError

# status: how ready a console is for a full decomp you can port and mod (the GUI and the README show it)
RECOMMENDED = "recommended"  # the most tested path, from import to a project that builds the game
BETA = "beta"  # works, but no real game has been rebuilt with it whole yet
PUBLIC = "public"  # only forks of public decomps: decompdecomp can't start its games from scratch
# mode: how a game of it is decompiled (the module docstring says more)
ROM = "rom"
PROJECT = "project"
JUDGES = {ROM: "link and compare", PROJECT: "objdiff"}
# features a console has beyond the basics (with its processor's, arch.COMPILER_MAP and the rest)
EXPORT = "export"  # "Export project" writes a tree that rebuilds the game byte for byte
REBUILD_CHECK = "rebuild_check"  # the project's build can be checked against the user's copy (Overview: rebuild)
HEAD_BYTES = 0x10000  # how much of a file sniff sees: a CD's file system starts at 0x9318 in a raw image


@dataclass(frozen=True)
class Compiler:
    """A compiler a console's games were built with."""
    id: str  # ROM mode: a toolchain in toolchains.toml; PROJECT mode: a version the project's build knows ("GC/1.3.2")
    name: str  # what the app shows: "KMC GCC 2.7.2"
    built: str = ""  # which games it built, for Setup: "many third-party N64 games"
    guess: str = ""  # what read_game calls it when a program looks built by it ("KMC", "EEGCC-2.95.2")


class Console:
    """One console: the facts below, and the parts the app calls. A part a console doesn't need keeps the default
    here, which does nothing or says the console can't do it."""

    id = ""  # what project configs store as their platform ("n64")
    name = ""  # what the app calls it: "Nintendo 64"
    short = ""  # where space is short (the sidebar): "N64"
    aliases: tuple[str, ...] = ()  # other ids older configs or public decomps use for it ("win32" for "pc")
    status = PUBLIC  # RECOMMENDED, BETA or PUBLIC
    arch = ""  # its processor, an id in arch/ ("mips")
    big_endian = False
    mode = ""  # ROM, PROJECT, or "" (only public decomps)
    layout = ""  # what a game started from scratch becomes, for the GUI: "splat", "dtk-template"
    compilers: tuple[Compiler, ...] = ()  # the first is the default, unless default_compiler says another
    packs: tuple[tuple[str, str], ...] = ()  # (toolchain, what it's for): more that Setup offers for it
    guides: tuple[str, ...] = ()  # guide topics worth reading first (files in its guides/ folder or app/guide/)
    m2c = ""  # m2c's target for drafts of its code, without the language ("mipsee-gcc"); "" when m2c can't read it
    compiler_guide = ""  # the guide on matching what most of its games were built with ("mwcc"): see up_front_guide
    compiler_family = ""  # their family, when a project names no toolchain (a fork): the permuter's weights ("mwcc")
    ram = (0, 0)  # where its programs' code and data live: words in this range read as pointers
    demo = ""  # its homemade demo game: a folder in projects/ whose build.py makes it (tests/test_consoles.py)
    demo_tools: tuple[str, ...] = ()  # what building and importing the demo needs: toolchains, or programs on PATH
    own_features: frozenset[str] = frozenset()  # EXPORT, REBUILD_CHECK
    # ROM mode's export: the assembler's flags for its processor, the rebuilt image's extension, and what every
    # assembly file of the export starts with
    as_flags: tuple[str, ...] = ()
    image_ext = ".bin"
    asm_preamble = '.include "macro.inc"\n.set noat\n.set noreorder\n.section .text, "ax"\n'

    def __init__(self, **facts):
        for key, value in facts.items():  # a console with only facts (consoles/public.py)
            if not hasattr(type(self), key):
                raise TypeError(f"a console has no fact {key!r}")
            setattr(self, key, value)

    def __repr__(self) -> str:
        return f"<console {self.id}>"

    @property
    def features(self) -> frozenset[str]:
        """What it can do beyond the basics: its own features and its processor's readers."""
        return self.own_features | arch.get(self.arch).features

    @property
    def judge(self) -> str:
        return JUDGES.get(self.mode, "objdiff")

    # ------------------------------------------------------------------ starting a project from the user's game

    def sniff(self, head: bytes, file: bytes | Path) -> bool:
        """Whether the file the user picked is a game of this console (`head`: its first HEAD_BYTES)."""
        return False

    def import_game(self, net, file: bytes | Path, *, toolchain: str | None = None, flags: list[str] | None = None,
                    name: str = "", log=print) -> str:
        """Start a project from the user's own copy of the game; returns its slug. The mode's way by default."""
        if self.mode == ROM:
            return net.import_split(self, self.read_game(file, name), toolchain=toolchain, flags=flags, log=log)
        if self.mode == PROJECT:
            path = Path(file) if isinstance(file, (str, Path)) else net.saved_file(file, name or self.upload_name(file))
            known = toolchain if toolchain in {c.id for c in self.compilers} else None  # (a ROM console's id: not ours)
            return net.forks.create_scratch(path, self, compiler=known, log=log)
        raise RomError(f"{self.name or 'these'} games can't be started from scratch in this version: continue a public "
                       "decomp of the game instead")

    def upload_name(self, data: bytes) -> str:
        """PROJECT mode: what a game sent as bytes is called on disk."""
        return "game.bin"

    # ROM mode

    def read_game(self, file: bytes | Path, name: str = "") -> Picked:
        """The program to split from the user's file, with what its header says (title, sha1, compiler_guess...)."""
        raise RomError(f"{self.name} games aren't split here")

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        """Which compiler built the game, from what its program says (info["compiler_guess"]) or splat's guess."""
        by_guess = {c.guess: c.id for c in self.compilers if c.guess}
        return by_guess.get(info.get("compiler_guess", ""), self.compilers[0].id if self.compilers else "")

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """A compiler's flags for a game of this console (`gp`: the game's $gp value, 0 when it has none)."""
        return list(toolchains.get(toolchain).flags)

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        """Before splat splits: code its own look misses (segments loaded later, overlays), added to its config.
        Returns the image splat reads."""
        return image

    def find_gp(self, image: bytes, program: bytes, segments: list) -> int:
        """$gp as the game sets it, when splat didn't say (0: the game keeps no data $gp-relative)."""
        return 0

    def debug_info(self, program: bytes, known: set[str]):
        """The game's own names, types and source files, when it shipped with them; None when it didn't."""
        return None

    def debug_brief(self, known: dict) -> list[str]:
        """A function's brief lines from what debug_info knew about it (stored with the function)."""
        return []

    def after_import(self, net, slug: str, *, chosen: bool, log=print) -> None:
        """Work a new project gets once (`chosen`: the user picked its compiler)."""

    def on_start(self, net, slug: str, log=print) -> None:
        """Work each of its projects gets when the app starts (what projects imported by older versions lack)."""

    # PROJECT mode

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        """Write the community's kind of project for the game into `root`: {"platform", "id", "title", "compiler"}."""
        raise RomError(f"{self.name} projects can't be made from scratch")

    def game_info(self, game: Path) -> dict:
        """What the game file is: {"platform", "id", "title", "sha1"}, the sha1 of what decides which game it is."""
        raise RomError(f"{self.name} projects can't be made from scratch")

    def tool_args(self) -> list[str]:
        """The arguments that point the project's configure step at this computer's pinned tools."""
        return []

    def identity(self, path: Path) -> str | None:
        """Which game a file is (a sha1), when that isn't the file's own bytes: rooms check members have the same."""
        return None

    def check_rebuild(self, fp, version: str, log=print) -> dict:
        """Build the project and compare what it makes with the user's copy: {"identical", "detail"}."""
        raise RomError(f"{self.name} projects aren't rebuilt and compared here")

    def read_data(self, fp, name: str, length: int) -> str | None:
        """A data symbol's bytes, when the project's objects don't hold the game's data; None: they do."""
        return None

    def file_compiler(self, fp, source: str) -> str | None:
        """The compiler (one of `compilers`) the project builds a source file with, when its build lets each file have
        its own: then try_compiler can switch it. None: the project's build decides alone."""
        return None

    def set_file_compiler(self, fp, source: str, compiler: str) -> str:
        """Make the project build a source file with `compiler` (file_compiler says whether it can); returns the file
        that says so, relative to the project (to commit)."""
        raise RomError(f"{self.name} projects set their compilers in their own build")

    def file_ranges(self, fp) -> dict[str, list[tuple[int, int]]]:
        """Where each source file's code is ({source: [(start, end)]}): the project Settings' address ranges, for a
        project whose files each have a compiler."""
        return {}

    def set_own_compiler(self, fp, compiler: str, sources: list[str]) -> list[str]:
        """Make `compiler` the project's own, every one of `sources` keeping the compiler it has; returns the files
        changed (relative to the project, to commit)."""
        raise RomError(f"{self.name} projects set their compilers in their own build")

    # for agents

    def asm_reason(self, code: bytes) -> str | None:
        """Why a function's code can't come from a compiler (hand-written assembly), or None."""
        reason = arch.get(self.arch).asm_reason
        return reason(code, self.big_endian) if reason else None

    def up_front_guide(self, compiler: str) -> str:
        """The guide every helper gets before its first claim: on matching `compiler`'s code ("" for none)."""
        return self.compiler_guide

    def m2c_target(self, compiler: str = "") -> str:
        """m2c's target for drafts of code `compiler` built, without the language; "" when m2c can't read it."""
        return self.m2c

    def project_facts(self) -> dict:
        """What a project's page and its line in the project list say about its console."""
        return {"platform": self.id, "platform_name": self.name, "platform_short": self.short or self.name,
                "layout": self.layout}

    def describe(self) -> dict:
        """What the GUI shows about it (the `consoles` list of /api/state)."""
        return {"id": self.id, "name": self.name, "short": self.short or self.name, "aliases": list(self.aliases),
                "status": self.status, "arch": self.arch, "mode": self.mode, "judge": self.judge, "layout": self.layout,
                "features": sorted(self.features), "packs": [tool for tool, _ in self.packs],
                "compilers": [{"id": c.id, "name": c.name, "built": c.built} for c in self.compilers],
                "default_compiler": self.default_compiler({}, "") if self.mode else ""}


def _registry() -> list[Console]:
    from .gc_wii import GAMECUBE, WII
    from .n64 import N64
    from .nds import NDS
    from .pc import PC
    from .ps1 import PS1
    from .ps2 import PS2
    from .public import PUBLIC_ONLY

    # the order sniff is asked in: a file is the first console's that says it's its own (the PS1 before the PS2,
    # whose sniff takes any ISO 9660 disc)
    return [N64, PS1, PS2, GAMECUBE, WII, PC, NDS, *PUBLIC_ONLY]


CONSOLES: list[Console] = _registry()
_BY_ID = {key: c for c in CONSOLES for key in (c.id, *c.aliases)}


def get(platform: str | None) -> Console:
    """The console a project's platform names (old ids too); an unknown one is a console with only that name."""
    return _BY_ID.get(platform or "") or Console(id=platform or "", name=platform or "")


def of(cfg: dict) -> Console:
    """The console of a project, from its settings. ROM projects from before configs named their platform are N64
    ones."""
    return get(cfg.get("platform") or ("n64" if cfg.get("kind") == "rom" else ""))


def name(platform: str | None) -> str:
    return get(platform).name


def startable() -> list[Console]:
    """The consoles whose games start from the user's own copy."""
    return [c for c in CONSOLES if c.mode]


def identify(file: bytes | Path) -> Console:
    """The console of the file the user picked (bytes, or a file on disk read in place: discs are gigabytes)."""
    if isinstance(file, (str, Path)):
        with open(file, "rb") as f:
            head = f.read(HEAD_BYTES)
        file = Path(file)
    else:
        head = bytes(file[:HEAD_BYTES])
    for console in startable():
        if console.sniff(head, file):
            return console
    supported = ", ".join(f"{c.name} ({c.layout})" for c in startable())
    raise RomError(f"this isn't a game file decompdecomp can start from scratch: it reads {supported}. For other "
                   "consoles, continue a public decomp")


def compiler(compiler_id: str) -> tuple[Console, Compiler] | None:
    """The console a compiler belongs to, and its entry there."""
    return next(((c, k) for c in CONSOLES for k in c.compilers if k.id == compiler_id), None)


def compiler_name(compiler_id: str) -> str:
    """A compiler's short name, from the console that lists it (its toolchain's name otherwise)."""
    found = compiler(compiler_id)
    if found:
        return found[1].name
    try:
        return toolchains.get(compiler_id).name
    except (KeyError, toolchains.ToolchainError):
        return compiler_id


def guide_dirs() -> list[Path]:
    """Where guides for agents are: each console's guides/ folder."""
    here = Path(__file__).parent
    return sorted(p for p in here.glob("*/guides") if p.is_dir())
