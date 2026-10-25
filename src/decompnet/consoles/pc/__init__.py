"""Windows PC games: 32-bit x86 programs (.exe) built with Microsoft Visual C++ 6.0 to 2010, PROJECT mode. Only
the game is a Windows program: decompdecomp runs on Linux, and so do the compilers (through wibo).

- scratch.py: a project for the user's .exe: Ghidra finds its functions, decompdecomp joins the pieces Ghidra cut
  off them back to them (merge_pieces, by arch/x86.piece) and cuts each one out of the program into a COFF object
  of its own (the target objdiff compares with); which Visual C++ built it comes from the program's Rich header,
  which also says (with its sections) whether decompdecomp can start it at all: 64-bit, Visual C++ 2017 and newer,
  mostly whole-program optimised, or wrapped in DRM (scratch.describe). The rebuild check puts the program's code
  back together from those objects (check_code). Visual C++'s exception-handling stubs are set aside
  (exception_stubs). Projects made before get what the import learnt since when the app starts (on_start, ANALYSIS).
  Each source file can have its own Visual C++ (file_compiler, set_file_compiler: config/<version>/file_compilers.json)
  and split_file divides one (split_source: a unit of functions.json).
- rtti.py: the C++ classes the program's run-time type information names (get_class, drafts as `Class::method`).
- debuginfo.py, pdb.py: the game's own debug information, when it has it (a .pdb beside it, the linker's .map, COFF
  symbols): real names, the game's source files, types for include/debug_types.h and prototypes for briefs
  (debug_of).
- project_template/: what that project starts as (tools/configure.py writes its ninja build).
- guides/x86.md: what agents read about x86 and matching Visual C++'s code.

Its demo game is projects/demo-pc (the Demo Quest game built with Visual C++ 6.0, or with any compiler of the
packs: build.py 10.0SP1). Older projects' configs say "win32" (decomp.dev's id): that's this console too.
"""

from __future__ import annotations

from pathlib import Path

from ...network import NetworkError
from .. import BETA, PROJECT, REBUILD_CHECK, Compiler, Console

MSVC_VERSIONS = ["MSVC/6.0", "MSVC/7.0", "MSVC/7.1",  # the `msvc` pack
                 "MSVC/8.0", "MSVC/8.0SP1", "MSVC/9.0", "MSVC/9.0SP1", "MSVC/10.0", "MSVC/10.0SP1"]  # `msvc2005`


class WindowsPC(Console):
    id = "pc"
    name = "Windows PC"
    short = "PC"
    aliases = ("win32",)
    status = BETA
    arch = "x86"
    mode = PROJECT
    layout = "Ghidra + objdiff"
    compilers = tuple(Compiler(v, f"Visual C++ {v.removeprefix('MSVC/').replace('SP', ' SP')}") for v in MSVC_VERSIONS)
    packs = (("msvc", "Visual C++ 6.0, 7.0 and 7.1, the compilers of PC games (proprietary)"),
             ("msvc2005", "Visual C++ 2005, 2008 and 2010, the compilers of newer PC games (proprietary, 600 MB)"),
             ("ghidra", "finds a PC game's functions, and drafts for platforms m2c can't read (540 MB, needs Java 21+)"))
    guides = ("x86", "cpp")
    compiler_guide = "x86"
    ram = (0x00400000, 0x80000000)  # a 32-bit program's address space above its usual image base
    demo = "demo-pc"
    demo_tools = ("msvc", "objdiff-cli", "wibo", "ghidra", "java")  # Ghidra needs Java 21 or newer
    own_features = frozenset({REBUILD_CHECK})  # the program's code, rebuilt from its delinked functions
    # what a project's config holds, in its manifest's "analysis" (on_start brings one with less up, step by step):
    # 1 (or none: every project made before 2) Ghidra's functions as it found them; 2 no jump table's entries
    # relocated twice (scratch.drop_repeated_relocations), no far call relocated (drop_far_call_relocations) and
    # every function's last instruction whole (complete_last_instructions); 3 Visual C++'s exception-handling stubs set aside (scratch.mark_exception_stubs);
    # 4 the pieces of functions Ghidra cut off joined to them (scratch.merge_pieces), never a stub. Its build tools
    # have a version of their own (the manifest's "tools": scratch.tools_version), so each change to them reaches it
    ANALYSIS = 4

    def sniff(self, head: bytes, file) -> bool:
        """Any Windows program, 64-bit ones too: the import says exactly why one it can't start can't start."""
        from .scratch import is_windows_program

        return is_windows_program(head)

    def upload_name(self, data: bytes) -> str:
        return "game.exe"

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        return ""  # the program's Rich header says which Visual C++ built it (scratch.guess_compiler)

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
        """A project made by an older decompdecomp gets what its import has learnt since, one step at a time from the
        level its manifest's "analysis" says (ANALYSIS lists them), each through forkengine.reanalyse: one commit and
        one line in the project's feed per step. A step that changes code (the repairs, the joins) is kept only when
        the program's code still rebuilds byte for byte from the delinked functions (check_rebuild), else taken back.
        The repairs go first: without them the rebuild can't be identical, and the joins need it to be. The stubs
        come next, found on the repaired functions, so the joins never take one (the function before the run, a C
        runtime function with a frame, would look continued by a funclet that reads ebp). The functions that grew
        are open again, with a note: what set them aside, and older notes, were about a part. A project without the
        user's copy of the program in it yet waits for the next start: every step reads it. Last, a project whose
        build tools aren't the template's (its manifest's "tools", scratch.tools_version) gets them, the same way:
        one commit and feed line, kept only when the rebuild check stays as it was, and tried once per version."""
        from . import scratch

        if not net.fork_slug(slug):
            return
        fp = net.forks.get(slug)[1]
        manifest = fp.manifest or {}
        if manifest.get("created") != "from scratch" or (manifest.get("analysis", 1) >= self.ANALYSIS and
                                                          manifest.get("tools") == scratch.tools_version()):
            return
        version = manifest["version"]
        if not (fp.root / manifest.get("game", {}).get("path", f"orig/{version}.exe")).is_file():
            return

        def matched() -> set[str]:  # not into or out of a matched function: its C would no longer be the whole one
            return {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND "
                                                     "status='matched'", net.project_row(slug)["id"])}

        def repairs(fp):
            n = scratch.drop_repeated_relocations(fp.root, version)
            far = scratch.drop_far_call_relocations(fp.root, version)
            cut = scratch.complete_last_instructions(fp.root, version, keep=matched())
            said = [f"{n} functions had a jump table's entries relocated twice, and once now"] if n else []
            if far:
                said.append(f"{far} functions had a far call's address taken for a call to another function, and keep "
                            "it as it is now")
            if cut:
                said.append(f"{cut} functions that ended in the middle of their last instruction have all of it now")
            return "; ".join(said) or None

        def stubs(fp):
            n = scratch.mark_exception_stubs(fp.root, version)
            return (f"{n} functions are exception-handling code Visual C++ makes from the function they belong to "
                    "(its .text$x): set aside, as no C of their own makes them" if n else None)
        joined: dict[str, list[str]] = {}

        def pieces(fp):
            joined.update(scratch.merge_project_pieces(fp.root, version, keep=matched(), log=log))
            n = sum(len(v) for v in joined.values())
            return f"{n} pieces of functions Ghidra cut off are part of the function they belong to again" if n else None
        def tools(fp):  # (no source file changes: agents' C stays as it is)
            changed = scratch.write_tools(fp.root, version)
            return (f"its build has the fixes decompdecomp's has had since the project was made ({', '.join(changed)})"
                    if changed else None)
        # (fix for the stubs: a step that changes no code can't break the rebuild, so it's kept where the rebuild
        # isn't identical yet too)
        steps = {2: (repairs, False), 3: (stubs, True), 4: (pieces, False)}
        kept = {}
        for level, (step, fix) in steps.items():
            if manifest.get("analysis", 1) < level:
                kept[level] = net.forks.reanalyse(slug, step, fix=fix, log=log)
                net.forks.get(slug)[1].update_manifest(analysis=level)  # tried: a step taken back isn't tried again
        if manifest.get("tools") != scratch.tools_version():
            net.forks.reanalyse(slug, tools, paths=("tools",), log=log)
            net.forks.get(slug)[1].update_manifest(tools=scratch.tools_version())  # (tried: not again until they change)
        if kept.get(4):  # (taken back: nothing was joined)
            agent, _ = net.local_agent("decompdecomp")
            for name, took in joined.items():
                net.joined(name, slug)
                try:
                    net.add_note(agent, slug, f"decompdecomp joined {', '.join(took)} to this function: it's the whole "
                                              "function now, not a fragment, whatever older notes say. Worth a try.",
                                 name)
                except NetworkError:  # gone meanwhile
                    pass

    def check_rebuild(self, fp, version: str, log=print) -> dict:
        from . import scratch

        return scratch.check_code(fp.root, version, log=log)

    def join_functions(self, fp, first: str, last: str, keep: set[str], log=print) -> str:
        from . import scratch

        return scratch.join_functions(fp.root, (fp.manifest or {})["version"], first, last, keep=keep)

    splits_sources = True  # a source file is a unit of functions.json (scratch.split_unit)

    def split_source(self, fp, version: str, source: str, at: str, new: str) -> dict:
        from . import scratch

        return scratch.split_unit(fp.root, version, source, at, new)

    # Each source file can have its own Visual C++ (config/<version>/file_compilers.json, which tools/configure.py
    # reads): a game's Rich header often counts objects of two versions (6.0 and 7.1), as DS games mix CodeWarrior's
    def file_compiler(self, fp, source: str) -> str | None:
        from . import scratch

        manifest = fp.manifest or {}
        # a project made before (its configure.py reads no map until on_start brings its tools up to date)
        if "version" not in manifest or not scratch.compilers_file(fp.root, manifest["version"]).exists():
            return None
        own = scratch.file_compilers(fp.root, manifest["version"]).get(source)
        return f"MSVC/{own}" if own else manifest.get("compiler", f"MSVC/{scratch.MSVC_DEFAULT}")

    def set_file_compiler(self, fp, source: str, compiler: str) -> str:
        from . import scratch

        manifest = fp.manifest or {}
        scratch.set_file_compiler(fp.root, manifest["version"], source, compiler,
                                  manifest.get("compiler", f"MSVC/{scratch.MSVC_DEFAULT}"))
        return scratch.compilers_file(fp.root, manifest["version"]).relative_to(fp.root).as_posix()

    def file_ranges(self, fp) -> dict[str, list[tuple[int, int]]]:
        from . import scratch

        manifest = fp.manifest or {}
        return scratch.file_ranges(fp.root, manifest["version"]) if "version" in manifest else {}

    def set_own_compiler(self, fp, compiler: str, sources: list[str]) -> list[str]:
        from . import scratch

        return scratch.set_own_compiler(fp.root, fp.manifest or {}, compiler, sources)

    def read_data(self, fp, name: str, length: int) -> str | None:
        """The program's data isn't in the target objects: it's read from the user's copy."""
        from . import scratch

        return scratch.read_data(fp.root, fp.manifest or {}, name, length)

    def data_bytes(self, fp, addr: int, length: int) -> bytes | None:
        from . import scratch

        return scratch.data_bytes(fp.root, fp.manifest or {}, addr, length)

    def debug_of(self, fp, version: str, symbol: str) -> dict | None:
        """The prototype, parameters and locals the game's program database gave a function at import
        (config/<version>/debug.json, debuginfo.py)."""
        from . import scratch

        return scratch.debug_of(fp.root, version, symbol)

    def debug_brief(self, known: dict) -> list[str]:
        from ..ps2.debuginfo import brief_lines

        return brief_lines(known, "include/debug_types.h (common.h includes it)")

    def classes(self, fp):
        from . import rtti

        return rtti.for_project(fp.root, fp.manifest or {})

    # The game's names are its addresses (func_0041B470, D_0066DDA4): objdiff pairs a function with the original's by
    # the symbol tools/names.py gives it, and the report keeps that symbol. A real name is kept by address in
    # functions.json's "names", which names.py reads too, so a renamed function still pairs (and so do its callers).
    renames_in_report = False
    names_structors = True  # names.py pairs ??0CFoo@@... and ??1CFoo@@... with the function named CFoo::CFoo, ~CFoo

    def rename_symbol(self, fp, version: str, old: str, new: str) -> dict:
        """functions.json's "names": `old`'s address (func_0041B470, _func_0041B470@8, D_0066DDA4, or a real name
        given before) gets the name `new`."""
        import json

        from ...forks.project import ForkError

        path = fp.root / "config" / version / "functions.json"
        text = path.read_text(encoding="utf-8")
        info = json.loads(text)
        names = dict(info.get("names") or {})
        key = _address_key(old) or next((k for k, v in names.items() if v == old), None)
        functions = {f"func_{fn['addr']:08X}" for fns in info["units"].values() for fn in fns}
        if key is None or (key.startswith("func_") and key not in functions):
            raise ForkError(f"{old} isn't one of the game's functions or data (func_<address>, D_<address>)")
        if names.get(key) == new:  # named so already (each attempt at a constructor names it: ForkEngine._attempt)
            return {}
        if new in names.values() or _address_key(new):
            raise ForkError(f"{new} names another of the game's symbols already: pick another name")
        names[key] = new
        path.write_text(json.dumps({**info, "names": dict(sorted(names.items()))}, indent=1), encoding="utf-8")
        return {path: text}

    def real_names(self, fp, version: str) -> dict[str, str]:
        """(a function's report name is its entry's in functions.json: the target objects are cut out with it)"""
        info = _functions_json(fp, version)
        names = info.get("names") or {}
        return {fn["name"]: names[key] for fns in info.get("units", {}).values() for fn in fns
                if (key := f"func_{fn['addr']:08X}") in names}

    def generated_functions(self, fp, version: str) -> dict[str, str]:
        """functions.json's `generated` entries: the exception-handling stubs (scratch.mark_exception_stubs)."""
        return {fn["name"]: fn["generated"] for fns in _functions_json(fp, version).get("units", {}).values()
                for fn in fns if fn.get("generated")}

    def rename_pattern(self, old: str) -> str:
        """func_0041B470 however a source spells it (a report's _func_0041B470@8 is func_0041B470 there, a method's
        Town::func_0041B470 too); a real name given before as itself."""
        key = _address_key(old)
        return rf"\b_*{key}\b" if key else super().rename_pattern(old)


def _functions_json(fp, version: str) -> dict:
    """The project's config/<version>/functions.json, or {} (none, or unreadable)."""
    import json

    try:
        return json.loads((fp.root / "config" / version / "functions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _address_key(name: str) -> str | None:
    """func_XXXXXXXX / D_XXXXXXXX (upper case address) in a name, as tools/names.py keys them; None without one."""
    import re

    m = re.search(r"\b_*(func|D)_([0-9A-Fa-f]{8})(?![0-9A-Fa-f])", name)
    return f"{m.group(1)}_{m.group(2).upper()}" if m else None


PC = WindowsPC()
