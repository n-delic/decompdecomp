"""GameCube and Wii: two consoles on one processor (PowerPC, the Gekko and the Broadway) with one way of working:
a dtk-template project (decomp-toolkit splits the game) built with Metrowerks CodeWarrior, PROJECT mode.

- scratch.py: a dtk-template project for the user's game, from a disc image (ISO, GCM, RVZ, WIA, WBFS, CISO, GCZ)
  or a program (DOL): the game's id, its modules (RELs), the first build that must rebuild it byte for byte.
- guides/mwcc.md: what agents read about matching CodeWarrior's code.

dtk projects someone published are forked like any public decomp: forks/ reads their layout (forks/dtksplits.py).
Its demo game is projects/demo-gc (the Demo Quest game built with CodeWarrior GC 1.3.2).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import BETA, PROJECT, REBUILD_CHECK, Compiler, Console

GC_VERSIONS = ["GC/1.0", "GC/1.1", "GC/1.1p1", "GC/1.2.5", "GC/1.2.5n", "GC/1.3", "GC/1.3.2", "GC/1.3.2r", "GC/2.0",
               "GC/2.0p1", "GC/2.5", "GC/2.6", "GC/2.7", "GC/3.0a3", "GC/3.0a3.2", "GC/3.0a3.3", "GC/3.0a3.4",
               "GC/3.0a5", "GC/3.0a5.2"]
WII_VERSIONS = ["Wii/0x4201_127", "Wii/1.0RC1", "Wii/1.0a", "Wii/1.0", "Wii/1.1", "Wii/1.3", "Wii/1.5", "Wii/1.6",
                "Wii/1.7"]
DETAIL_BYTES = 300  # of a failed rebuild's last line, shown on the project
SMALL = 64 << 20  # a program (DOL) is smaller than this; a disc image is read in place


class GameCube(Console):
    id = "gc"
    name = "GameCube"
    short = "GameCube"
    status = BETA  # BETA until its conformance test passes and one real game rebuilt byte for byte (CodeWarrior and dtk
    # can't run in public CI, and nothing has been tested end to end yet)
    arch = "ppc"
    big_endian = True
    mode = PROJECT
    layout = "dtk-template"
    compilers = tuple(Compiler(v, f"CodeWarrior {v}") for v in GC_VERSIONS)
    packs = (("mwcc", "CodeWarrior, the compilers of GameCube and Wii games (proprietary)"),)
    guides = ("ppc", "mwcc", "cpp")
    compiler_guide = "mwcc"  # CodeWarrior's register and stack rules decide most of what's left
    compiler_family = "mwcc"
    m2c = "ppc-mwcc"
    ram = (0x80000000, 0x81800000)
    demo = "demo-gc"
    demo_tools = ("mwcc", "dtk", "gc-wii-binutils", "objdiff-cli", "sjiswrap", "dtk-template", "wibo")
    own_features = frozenset({REBUILD_CHECK})
    usual = "GC/1.3.2"  # what most of its games were built with: the default until the game says otherwise
    disc_kinds = ("gc", "packed")  # what scratch.disc_kind says its discs are (a compressed one can be either's)

    def sniff(self, head: bytes, file) -> bool:
        from . import scratch

        if scratch.disc_kind(head) in self.disc_kinds:
            return True
        if not isinstance(file, Path):
            return scratch.is_dol(bytes(file))
        return file.stat().st_size < SMALL and scratch.is_dol(file.read_bytes())

    def upload_name(self, data: bytes) -> str:
        from . import scratch

        return "main.dol" if scratch.is_dol(data) else "game.iso"

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        return self.usual

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        from . import scratch

        return scratch.create(root, game, compiler=compiler, log=log)

    def game_info(self, game: Path) -> dict:
        from . import scratch

        info = scratch.game_info(game)
        return info | {"sha1": hashlib.sha1(info["dol"]).hexdigest()}  # the main program decides which game it is

    def tool_args(self) -> list[str]:
        from . import scratch

        return scratch.tool_args()

    def identity(self, path: Path) -> str | None:
        """The disc's main program (any disc format; a DOL on its own too): the same game in another disc format is
        the same game."""
        from ...forks.project import ForkError
        from . import scratch

        with open(path, "rb") as f:
            head = f.read(0x440)
        try:
            if scratch.is_dol(head if path.stat().st_size >= SMALL else path.read_bytes()) or \
                    scratch.disc_kind(head) is not None:
                return hashlib.sha1(scratch.game_info(path)["dol"]).hexdigest()
        except ForkError:
            pass
        return None

    def check_rebuild(self, fp, version: str, log=print) -> dict:
        from . import scratch

        ok, tail = fp.verify(log=log)
        diff = scratch.differing_files(fp.root, version, tail) or []
        return {"identical": ok, "detail": "identical" if ok else f"{len(diff)} files differ: {', '.join(diff[:8])}"
                if diff else (tail.strip().splitlines() or ["the build failed"])[-1][-DETAIL_BYTES:]}


class Wii(GameCube):
    id = "wii"
    name = "Wii"
    short = "Wii"
    compilers = tuple(Compiler(v, f"CodeWarrior {v}") for v in WII_VERSIONS)
    ram = (0x80000000, 0x94000000)  # MEM1, then MEM2
    demo = ""  # none yet: the GameCube demo's tests cover the same code
    usual = "Wii/1.0"
    disc_kinds = ("wii",)  # its programs (DOLs) and compressed discs are taken for a GameCube's, and told apart later

    def sniff(self, head: bytes, file) -> bool:
        from . import scratch

        return scratch.disc_kind(head) in self.disc_kinds


GAMECUBE = GameCube()
WII = Wii()
