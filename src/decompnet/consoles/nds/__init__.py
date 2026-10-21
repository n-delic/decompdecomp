"""Nintendo DS: the ARM946E-S (ARM and Thumb), Metrowerks CodeWarrior for DS, PROJECT mode: a ds-decomp project, the
kind DS decomps on decomp.dev are.

- scratch.py: a ds-decomp project for the user's ROM (.nds): dsd extracts it, finds the functions of the ARM9
  program, its ITCM and DTCM parts and its overlays, and delinks them; decompdecomp cuts the code into source files
  and checks the rebuilt ROM against the user's copy.
- recover.py: the functions dsd's analysis misses (only pointers reach them), added to its config; projects made
  before it get them when the app starts (on_start, ANALYSIS). Each file can have its own CodeWarrior version
  (file_compiler, set_file_compiler: games mix them, issue #23).
- project_template/: what that project starts as (tools/configure.py writes its ninja build).
- guides/ds.md: what agents read about matching DS code.

Its demo game is projects/demo-ds (the Demo Quest game built with CodeWarrior for DS 2.0sp1p5).
"""

from __future__ import annotations

import json
from pathlib import Path

from ...network import NetworkError
from .. import BETA, PROJECT, REBUILD_CHECK, Compiler, Console

# the `mwccarm` pack's versions, oldest first
DS_VERSIONS = ["1.2/base", "1.2/sp2", "1.2/sp2p3", "1.2/sp3", "1.2/sp4", "2.0/base", "2.0/sp1", "2.0/sp1p2",
               "2.0/sp1p5", "2.0/sp1p6", "2.0/sp1p7", "2.0/sp2", "2.0/sp2p2", "2.0/sp2p3", "2.0/sp2p4", "dsi/1.1",
               "dsi/1.1p1", "dsi/1.2", "dsi/1.2p1", "dsi/1.2p2", "dsi/1.3", "dsi/1.3p1", "dsi/1.6sp1", "dsi/1.6sp2"]


class NintendoDS(Console):
    id = "nds"  # decomp.dev's id, which public DS decomps keep
    name = "Nintendo DS"
    short = "DS"
    status = BETA  # a real game rebuilds byte for byte, from its delinked code and agents' C; none decompiled whole yet
    arch = "arm"
    mode = PROJECT
    layout = "ds-decomp"
    compilers = tuple(Compiler(f"DS/{v}", f"CodeWarrior for DS {v}") for v in DS_VERSIONS)
    packs = (("mwccarm", "CodeWarrior for DS, the compilers of DS games (proprietary)"),)
    guides = ("arm", "mwcc", "ds")
    compiler_guide = "ds"
    compiler_family = "mwcc"
    m2c = "arm"
    ram = (0x01FF8000, 0x02800000)  # ITCM, main RAM and DTCM, as the ARM9 sees them
    demo = "demo-ds"
    demo_tools = ("mwccarm", "dsd", "arm-gnu", "objdiff-cli", "wibo")
    own_features = frozenset({REBUILD_CHECK})
    usual = "DS/2.0/sp1p5"  # what most of its games were built with: the default until the game says otherwise
    # what a project's config holds, in its manifest's "analysis": 1 dsd's own analysis, 2 with the functions it
    # misses (recover.py), 3 its build tools brought up to date too (scratch.write_tools: dead stripping dropped
    # overlays holding only data), 4 the code past the end dsd gave .text (recover.extend_text), 5 the pieces of
    # functions dsd cut off joined to their functions (recover.merge_pieces), 6 with the pieces only the caller's
    # registers tell (arm.piece: code using r4-r11 before saving them). A project with less gets the rest when
    # the app starts, or when it's checked again
    ANALYSIS = 6

    def sniff(self, head: bytes, file) -> bool:
        from .scratch import is_nds

        return is_nds(head)

    def upload_name(self, data: bytes) -> str:
        return "game.nds"

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        return self.usual

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        from . import scratch

        return scratch.create(root, game, compiler=compiler, log=log)

    def game_info(self, game: Path) -> dict:
        from . import scratch

        return scratch.game_info(game)

    def tool_args(self) -> list[str]:
        from . import scratch

        return scratch.tool_args()

    def on_start(self, net, slug: str, log=print) -> None:
        """A project made by an older decompdecomp gets, once, its build tools' fixes (scratch.write_tools), the
        functions dsd's analysis missed (recover.py: in .text's gaps and past its end) and the pieces of functions it
        cut off joined to them (recover.merge_pieces), through forkengine.reanalyse: checked by the rebuild, taken back
        if that breaks."""
        from . import recover, scratch

        if not net.fork_slug(slug):
            return
        fp = net.forks.get(slug)[1]
        manifest = fp.manifest or {}
        if manifest.get("created") != "from scratch" or manifest.get("analysis", 1) >= self.ANALYSIS:
            return

        def tools(fp):
            return ("its build has the fixes decompdecomp's has had since the project was made"
                    if scratch.write_tools(fp.root) else None)

        def functions(fp):  # again on a project that has them: what it adds is the code past .text's old end
            found = recover.recover(fp.root, manifest["version"], dsd=scratch.dsd_path(), log=log)
            files = scratch.add_sources(fp.root, manifest["version"])
            said = [f"{found['functions']} functions dsd's analysis had missed (only pointers reach them) are in its "
                    "source files now"] if found["functions"] else []
            if files:
                said.append(f"{len(files)} new source files hold the code dsd took for data past the end of .text")
            return "; ".join(said) or None
        joined: dict[str, list[str]] = {}

        def pieces(fp):  # not into or out of a matched function: its C would no longer be the whole function
            matched = {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND "
                                                        "status='matched'", net.project_row(slug)["id"])}
            joined.update(recover.merge_pieces(fp.root, manifest["version"], dsd=scratch.dsd_path(), keep=matched,
                                               log=log))
            n = sum(len(v) for v in joined.values())
            return (f"{n} pieces of functions dsd's analysis cut off are part of the function they belong to again"
                    if n else None)
        if manifest.get("analysis", 1) < 4:  # (a project at 4 or more has these already)
            net.forks.reanalyse(slug, tools, paths=("tools",), fix=True, log=log)  # first: the functions' check needs it
            net.forks.reanalyse(slug, functions, log=log)
        if net.forks.reanalyse(slug, pieces, log=log):
            # its notes from before say "fragment, skip": agents read the newest note first
            agent, _ = net.local_agent("decompdecomp")
            for name, took in joined.items():
                try:
                    net.add_note(agent, slug, f"decompdecomp joined {', '.join(took)} to this function: it's the whole "
                                              "function now, not a fragment, whatever older notes say. Worth a try.",
                                 name)
                except NetworkError:  # gone meanwhile
                    pass
        now = net.forks.get(slug)[1].manifest or {}  # with the rebuild reanalyse wrote
        (fp.root / ".decomp.wtf.json").write_text(json.dumps({**now, "analysis": self.ANALYSIS}, indent=2),
                                                 encoding="utf-8")

    def check_rebuild(self, fp, version: str, log=print) -> dict:
        from . import scratch

        return scratch.check_rom(fp.root, version, log=log)

    def file_compiler(self, fp, source: str) -> str | None:
        from . import scratch

        manifest = fp.manifest or {}
        # a public DS decomp, or a project 1.0.2 made (its configure.py reads no map): its own build decides
        if "version" not in manifest or not scratch.compilers_file(fp.root, manifest["version"]).exists():
            return None
        own = scratch.file_compilers(fp.root, manifest["version"]).get(source)
        return f"DS/{own}" if own else manifest.get("compiler", self.usual)

    def set_file_compiler(self, fp, source: str, compiler: str) -> str:
        from ...forks.project import ForkError
        from . import scratch

        manifest = fp.manifest or {}
        if "version" not in manifest:
            raise ForkError("this project's own build sets its compilers")
        scratch.set_file_compiler(fp.root, manifest["version"], source, compiler, manifest.get("compiler", self.usual))
        return scratch.compilers_file(fp.root, manifest["version"]).relative_to(fp.root).as_posix()

    def file_ranges(self, fp) -> dict[str, list[tuple[int, int]]]:
        from . import scratch

        manifest = fp.manifest or {}
        return scratch.file_ranges(fp.root, manifest["version"]) if "version" in manifest else {}

    def set_own_compiler(self, fp, compiler: str, sources: list[str]) -> list[str]:
        from ...forks.project import ForkError
        from . import scratch

        manifest = fp.manifest or {}
        if "version" not in manifest:
            raise ForkError("this project's own build sets its compilers")
        return scratch.set_own_compiler(fp.root, manifest, compiler, sources)


NDS = NintendoDS()
