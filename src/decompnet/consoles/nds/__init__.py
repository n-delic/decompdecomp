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
    # registers tell (arm.piece: code using r4-r11 before saving them), 7 a switch's cases in the next source file
    # moved to its own (recover.move_boundaries), 8 pieces joined with the bytes no symbol claims between them, 9 a
    # function pieces were joined to open again when what set it aside was about a piece (Network.joined). A project with less gets the rest when
    # the app starts, or when it's checked again
    ANALYSIS = 9

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
        def boundaries(fp):  # its own step: a rebuild it breaks takes back only these
            matched = {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND "
                                                        "status='matched'", net.project_row(slug)["id"])}
            moved = recover.move_boundaries(fp.root, manifest["version"], keep=matched, log=log)
            return (f"{len(moved)} functions a switch runs on into ({', '.join(moved[:3])}{'…' if len(moved) > 3 else ''})"
                    " moved to the source file of the function they're part of" if moved else None)
        level = manifest.get("analysis", 1)
        if level < 4:  # (a project at 4 or more has these already)
            net.forks.reanalyse(slug, tools, paths=("tools",), fix=True, log=log)  # first: the functions' check needs it
            net.forks.reanalyse(slug, functions, log=log)
        net.forks.reanalyse(slug, boundaries, log=log)
        net.forks.reanalyse(slug, pieces, log=log)
        # its notes from before say "fragment, skip": agents read the newest note first (and a project joined before
        # these notes were written gets them now, once)
        # and what set them aside was about a piece (Network.joined): once for the functions joined before (9)
        earlier = recover.joined_pieces(fp.root, manifest["version"]) if level < 9 else {}
        agent, _ = net.local_agent("decompdecomp")
        for name, took in (earlier | joined).items():
            net.joined(name, slug)
            if name in joined or level < 6:
                try:
                    net.add_note(agent, slug, f"decompdecomp joined {', '.join(took)} to this function: it's the whole "
                                              "function now, not a fragment, whatever older notes say. Worth a try.",
                                 name)
                except NetworkError:  # gone meanwhile
                    pass
        now = net.forks.get(slug)[1].manifest or {}  # with the rebuild reanalyse wrote
        (fp.root / ".decomp.wtf.json").write_text(json.dumps({**now, "analysis": self.ANALYSIS}, indent=2),
                                                 encoding="utf-8")

    splits_sources = True

    def rename_symbol(self, fp, version: str, old: str, new: str) -> dict:
        """ds-decomp's symbols.txt: the line `old kind:...` becomes `new kind:...` (the delinked objects take it)."""
        import re

        from ...forks.project import ForkError

        saved = {}
        for path in sorted((fp.root / "config" / version).rglob("symbols.txt")):
            text = path.read_text(encoding="utf-8")
            if re.search(rf"^{re.escape(new)} kind:", text, re.M):
                raise ForkError(f"{new} is a symbol of the game already")
            changed = re.sub(rf"^{re.escape(old)}(?= kind:)", new, text, flags=re.M)
            if changed != text:
                saved[path] = text
                path.write_text(changed, encoding="utf-8")
        if not saved:
            raise ForkError(f"{old} isn't one of the game's symbols")
        return saved

    def split_source(self, fp, version: str, source: str, at: str, new: str) -> dict:
        """The source file's block in its module's delinks.txt ends where function `at` starts, and `new`'s, from
        there to where it ended, follows it (its data stays with the file); `new` gets the file's compiler
        (file_compilers.json)."""
        import re

        from ...forks.project import ForkError
        from . import recover, scratch

        where = [int(s.group(4), 16) for p in (fp.root / "config" / version).rglob("symbols.txt")
                 for ln in p.read_text(encoding="utf-8").splitlines()
                 if (s := recover.SYMBOL.match(ln)) and s.group(1) == at and s.group(2) == "function"]
        if len(where) != 1:
            raise ForkError(f"{at} isn't one function of the game's config")
        at_addr = where[0]
        saved = {}
        for path in sorted((fp.root / "config" / version).rglob("delinks.txt")):
            text = path.read_text(encoding="utf-8")
            m = re.search(rf"^{re.escape(source)}:\n((?:[ \t]+\S.*\n?)*)", text, re.M)
            if not m:
                continue
            t = re.search(r"^(\s+\.text start:)(0x[0-9a-fA-F]+) end:(0x[0-9a-fA-F]+)", m.group(1), re.M)
            if not t or not int(t.group(2), 16) < at_addr < int(t.group(3), 16):
                raise ForkError(f"{at} isn't in {source}'s code after its start")
            block = m.group(0).rstrip("\n").replace(t.group(0), f"{t.group(1)}{t.group(2)} end:{at_addr:#010x}", 1)
            new_block = f"{new}:\n    .text start:{at_addr:#010x} end:{t.group(3)}"
            saved[path] = text
            path.write_text(text[:m.start()] + block + "\n\n" + new_block + "\n" + text[m.end():], encoding="utf-8")
            break
        else:
            raise ForkError(f"{source} isn't one of the project's source files")
        compilers = scratch.compilers_file(fp.root, version)
        if compilers.exists():
            saved[compilers] = compilers.read_text(encoding="utf-8")
            own = json.loads(saved[compilers])
            if source in own:
                compilers.write_text(json.dumps({**own, new: own[source]}, indent=2) + "\n", encoding="utf-8")
        return saved

    def join_functions(self, fp, first: str, last: str, keep: set[str], log=print) -> str:
        from . import recover, scratch

        return recover.join_functions(fp.root, (fp.manifest or {})["version"], first, last, keep=keep,
                                      dsd=scratch.dsd_path())

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
