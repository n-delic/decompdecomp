"""Windows PC games: 32-bit x86 programs (.exe) built with Microsoft Visual C++ 6.0 to 2010, PROJECT mode. Only
the game is a Windows program: decompdecomp runs on Linux, and so do the compilers (through wibo).

- scratch.py: a project for the user's .exe: Ghidra finds its functions, decompdecomp joins the pieces Ghidra cut
  off them back to them (merge_pieces, by arch/x86.piece) and cuts each one out of the program into a COFF object
  of its own (the target objdiff compares with); which Visual C++ built it comes from the program's Rich header,
  which also says (with its sections) whether decompdecomp can start it at all: 64-bit, Visual C++ 2017 and newer,
  mostly whole-program optimised, or wrapped in DRM (scratch.describe). The rebuild check, which is also the
  project's export, puts the whole program back together from the project (`ninja rebuild`, check_code): each
  function from its C where that compiles to the game's code, else from those objects. Visual C++'s
  exception-handling stubs are set aside (exception_stubs), and so are the import thunks, named after their imports
  (name_import_thunks). Projects made before get what the import learnt since when the app starts (on_start,
  ANALYSIS).
  Each source file can have its own Visual C++ (file_compiler, set_file_compiler: config/<version>/file_compilers.json)
  and split_file divides one (split_source: a unit of functions.json). The game's own DLLs beside the .exe are
  programs of the project too (find_modules; a game given as its folder: program_in), each in a folder of src/auto/.
- rtti.py: the C++ classes the program's run-time type information names, those of its code built without it (from
  the vtables constructors store), their fields and the struct layouts of every function's pointers, read by
  arch/x86_fields.py (get_class, guess_struct, drafts as `Class::method`).
- debuginfo.py, pdb.py: the game's own debug information, when it has it (a .pdb beside it, the linker's .map, COFF
  symbols): real names, the game's source files, types for include/debug_types.h and prototypes for briefs
  (debug_of).
- drafts.py: what autopilot tries where m2c can't read x86: exact drafts of the tiny shapes Visual C++ makes, read from
  the code, and Ghidra's drafts made into C++ that compiles in the project (also in briefs, the background drafter and
  redraft). Each file may be built with more flags (file_flags: /Oy- where its functions keep an ebp frame, found by
  the import: scratch.frame_flags), which search_flags tries.
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
    # the whole program rebuilt from the project: like GameCube's and DS's, the project is the export
    own_features = frozenset({REBUILD_CHECK})
    reimports = True  # Ghidra again, read by the newest rules, matches kept (reimport)
    # what a project's config holds, in its manifest's "analysis" (on_start brings one with less up, step by step):
    # 1 (or none: every project made before 2) Ghidra's functions as it found them; 2 no jump table's entries
    # relocated twice (scratch.drop_repeated_relocations), no far call relocated (drop_far_call_relocations) and
    # every function's last instruction whole (complete_last_instructions); 3 Visual C++'s exception-handling stubs set aside (scratch.mark_exception_stubs);
    # 4 the pieces of functions Ghidra cut off joined to them (scratch.merge_pieces), never a stub; 5 the import thunks
    # functions of their own, named after their imports and set aside (scratch.name_import_thunks); 6 the files whose
    # functions keep an ebp frame built with /Oy- (scratch.set_frame_flags), but for files with matched code: after
    # the thunks, which keep no frame and would count against their file's. Its build tools have a version of their
    # own (the manifest's "tools": scratch.tools_version), so each change to them reaches it
    ANALYSIS = 6

    def sniff(self, head: bytes, file) -> bool:
        """Any Windows program, 64-bit ones too: the import says exactly why one it can't start can't start."""
        from .scratch import is_windows_program

        return is_windows_program(head)

    def upload_name(self, data: bytes) -> str:
        return "game.exe"

    def program_in(self, folder: Path) -> Path | None:
        """A PC game is a folder: its .exe, and the DLLs of its own create takes along (scratch.find_modules)."""
        from .scratch import program_in

        return program_in(folder)

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
        if not (fp.root / manifest.get("game", {}).get("path", f"orig/{version}.exe")).is_file() or \
                scratch.missing(fp.root, version):  # (the game's DLLs too: each step reads every program)
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
        def thunks(fp):
            n = scratch.name_import_thunks(fp.root, version, keep=matched())
            return (f"{n} functions are import thunks the linker adds for calls to DLLs' functions: named after the "
                    "function each calls, and set aside" if n else None)

        def frames(fp):  # (files with matched code keep their flags: that C matches with them)
            done = {scratch._source(fp.root, r["unit"]) for r in net.store.all(
                "SELECT DISTINCT unit FROM functions WHERE project_id=? AND status='matched'", net.project_row(slug)["id"])}
            n = scratch.set_frame_flags(fp.root, version, keep=done)
            return (f"{n} source files whose functions keep an ebp frame build with {scratch.FRAME_FLAG} now (Visual "
                    "C++ keeps the frame pointer), as the game's were" if n else None)

        def tools(fp):  # (no source file changes: agents' C stays as it is)
            changed = scratch.write_tools(fp.root, version)
            return (f"its build has the fixes decompdecomp's has had since the project was made ({', '.join(changed)})"
                    if changed else None)
        # (fix for the stubs: a step that changes no code can't break the rebuild, so it's kept where the rebuild
        # isn't identical yet too)
        steps = {2: (repairs, False), 3: (stubs, True), 4: (pieces, False), 5: (thunks, False), 6: (frames, True)}
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

    def reimport(self, net, slug: str, game: Path, log=print) -> str | None:
        """Ghidra's analysis again, read by this decompdecomp's rules (scratch.reanalyse_program), of the program and
        each of the game's DLLs in the project, through forkengine.reanalyse: kept only when every program's code
        still rebuilds byte for byte."""
        import hashlib

        from ...forks import ghidra
        from . import scratch

        fp = net.forks.get(slug)[1]
        manifest = fp.manifest or {}
        if hashlib.sha1(game.read_bytes()).hexdigest() != manifest.get("game", {}).get("sha1", ""):
            raise NetworkError("that isn't this project's program (its SHA-1 differs): choose the same .exe, or start "
                               "a new project for another one")
        if scratch.missing(fp.root, manifest["version"]):
            raise NetworkError("the project is missing the game's DLLs it was made with (in orig/): put your copies "
                               "there, then reimport")
        log(f"analysing {game.name} with Ghidra again (a big game takes a few minutes)")
        found = {"": ghidra.functions(game)}
        for m in _functions_json(fp, manifest["version"]).get("modules", []):
            log(f"analysing {m['exe'].removeprefix('orig/')} with Ghidra again")
            found[m["name"]] = scratch.module_rows(ghidra, fp.root / m["exe"], m["image_base"] - m["preferred_base"])
        matched = {r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND "
                                                     "status='matched'", net.project_row(slug)["id"])}
        return net.forks.reanalyse(slug, lambda fp: scratch.reanalyse_program(fp.root, manifest["version"], found,
                                                                                keep=matched), log=log)

    def check_rebuild(self, fp, version: str, log=print) -> dict:
        """The program rebuilt from what the project builds now: its objects first (a file that no longer compiles
        keeps the object it had, whose functions are checked against the game's code as any)."""
        from . import scratch

        fp.run(["ninja", "prepare"], log=lambda *_: None, timeout=scratch.TOOL_SECONDS)
        return scratch.check_code(fp.root, version, log=log)

    def join_functions(self, fp, first: str, last: str, keep: set[str], log=print) -> str:
        from . import scratch

        return scratch.join_functions(fp.root, (fp.manifest or {})["version"], first, last, keep=keep)

    def function_extent(self, fp, symbol: str) -> tuple[int, int] | None:
        from . import scratch

        manifest = fp.manifest or {}
        return scratch.function_extent(fp.root, manifest["version"], symbol) if "version" in manifest else None

    def cut_function(self, fp, symbol: str, at: int, keep: set[str], why: str = "") -> str:
        from . import scratch

        return scratch.cut_function(fp.root, (fp.manifest or {})["version"], symbol, at, keep=keep, why=why)

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

    def file_flags(self, fp, source: str) -> list[str] | None:
        from . import scratch

        version = (fp.manifest or {}).get("version")
        if not version or not scratch.flags_file(fp.root, version).exists():  # older tools: no flags of its own yet
            return None
        return scratch.file_flags(fp.root, version).get(source, [])

    def set_file_flags(self, fp, source: str, flags: list[str]) -> str:
        from . import scratch

        version = (fp.manifest or {})["version"]
        scratch.set_file_flags(fp.root, version, source, flags)
        return scratch.flags_file(fp.root, version).relative_to(fp.root).as_posix()

    # What search_flags tries on a file: the frame pointer kept or not (/Oy-: about half of a 2010 game's code keeps
    # it), optimised for size (/O1: some of a game's libraries were), and inlining only what's marked inline (/Ob1)
    FLAG_TOGGLES = (("/Oy-",), ("/O1",), ("/Ob1",), ("/O1", "/Oy-"))

    def flag_variants(self, flags: list[str]) -> list[list[str]]:
        out = [list(flags)]
        for toggle in self.FLAG_TOGGLES:
            new = [f for f in flags if f not in toggle] if all(f in flags for f in toggle) else \
                [*flags, *(f for f in toggle if f not in flags)]
            if new not in out:
                out.append(new)
        return out

    def file_ranges(self, fp) -> dict[str, list[tuple[int, int]]]:
        from . import scratch

        manifest = fp.manifest or {}
        return scratch.file_ranges(fp.root, manifest["version"]) if "version" in manifest else {}

    def program_compilers(self, fp) -> list[tuple[str, int, str, bool]]:
        """The Rich header's compilers: a game of about 2000 counts files of five Visual C++ 6.0 builds (its own
        code, MFC's, the C runtime's), a later one 6.0's and 7.1's. The game's DLLs' headers count too."""
        from . import scratch

        manifest = fp.manifest or {}
        path = fp.root / manifest.get("game", {}).get("path", f"orig/{manifest.get('version')}.exe")
        paths = [path, *(fp.root / m["exe"] for m in _functions_json(fp, manifest.get("version", "")).get("modules", []))]
        counted: dict[str, list] = {}
        for found in (scratch.program_compilers(p.read_bytes()) for p in paths if p.is_file()):
            for compiler, n, name, installed in found:
                counted.setdefault(name, [compiler, 0, name, installed])[1] += n
        return sorted((tuple(row) for row in counted.values()), key=lambda row: -row[1])

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

    def structs(self, fp):
        from . import rtti

        return rtti.structs_for_project(fp.root, fp.manifest or {})

    # Autopilot's drafts: Ghidra's made compilable, and exact ones for the tiny shapes Visual C++ makes (drafts.py)
    own_drafts = True
    own_tools = frozenset({"redraft", "search_flags"})

    def prepare_drafts(self, fp) -> str | None:
        from .drafts import GHIDRA_H, GHIDRA_HEADER

        path = fp.root / "include" / GHIDRA_HEADER
        if path.exists():
            return None
        path.write_text(GHIDRA_H, encoding="utf-8")
        return path.relative_to(fp.root).as_posix()

    def exact_drafts(self, fp, symbol: str, known: str = "") -> list[tuple[str, str]]:
        from . import drafts, scratch

        entry = _entries(fp).get(symbol)
        code = entry and scratch.data_bytes(fp.root, fp.manifest or {}, entry["addr"], entry["size"])
        return drafts.exact(code, entry["addr"], entry["relocs"], known) if code else []

    def compilable_draft(self, fp, symbol: str, draft: str, known: str = "") -> tuple[str, str] | None:
        from . import drafts

        entries = _entries(fp)
        return drafts.compilable(draft, symbol, {f"func_{e['addr']:08X}": n for n, e in entries.items()}, known)

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


_ENTRIES: dict[str, tuple[int, dict]] = {}  # functions.json's path -> (its mtime, its functions by name)


def _entries(fp) -> dict[str, dict]:
    """Every function of the project's functions.json by its name, read once per change of the file (autopilot asks
    for each function's: a big game's file is megabytes)."""
    path = fp.root / "config" / (fp.manifest or {}).get("version", "") / "functions.json"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    if (kept := _ENTRIES.get(str(path))) is None or kept[0] != mtime:
        info = _functions_json(fp, (fp.manifest or {})["version"])
        kept = _ENTRIES[str(path)] = (mtime, {fn["name"]: fn for fns in info.get("units", {}).values() for fn in fns})
    return kept[1]


def _address_key(name: str) -> str | None:
    """func_XXXXXXXX / D_XXXXXXXX (upper case address) in a name, as tools/names.py keys them; None without one."""
    import re

    m = re.search(r"\b_*(func|D)_([0-9A-Fa-f]{8})(?![0-9A-Fa-f])", name)
    return f"{m.group(1)}_{m.group(2).upper()}" if m else None


PC = WindowsPC()
