"""Importing a game: its console (consoles/: the connector) says what the file is and how its games are imported.
ROM mode's import is here: split the program (splat), make every function's target and the project's first
header, with what the console adds (more code to split, the game's own symbols, a test of which compiler built it).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from ... import consoles, scoring, seeds, toolchains
from ...network import NetworkError
from ...project import compiler_for
from ...rom import Picked, RomError, match, split
from ...rom.symbols import AUTO_NAME, Symbol, SymbolTable
from .common import BASE_HEADER, GP_NOTE, data_preview, slugify

# what the first header defines already: the game's typedefs of these names are left out (consoles/ps2/cheader.py)
_HEADER_NAMES = {name for pair in re.findall(r"typedef [^;]*\b(\w+);|#define (\w+)", BASE_HEADER) for name in pair if name}
DEBUG_TYPES_NOTE = """
/* The game's own types, from the debug information it shipped with: each field is at the offset the game has it
 * (the comments say where). Use these names; don't declare them again. */
"""
DEBUG_TYPES_FILE = "debug_types.h"
HEADER_CHECK = "int decomp_header_check;"  # a source that needs nothing but the header to compile


class ImportMixin:
    def import_rom(self, data: bytes | Path, *, toolchain: str | None = None, flags: list[str] | None = None,
                   confirmed: bool = False, name: str = "", log=print, scope: str | None = None) -> str:
        """Import a game the user owns (ROM bytes, or a file such as a disc image): split it, store every function
        as a target. Returns the slug. `scope`: what the project covers, where its console sets library code aside
        ("game", the default, or "program": forks/project.py SCOPES)."""
        if not confirmed:
            raise NetworkError("confirm that this is your own legally obtained copy of the game")
        console = consoles.identify(data)
        return console.import_game(self, data, toolchain=toolchain, flags=flags, name=name, log=log, scope=scope)

    def saved_file(self, data: bytes | Path, name: str) -> Path:
        """A game sent as bytes, saved to a file first (PROJECT mode's tools read files); a file stays where it is."""
        if isinstance(data, (str, Path)):
            return Path(data)
        uploads = self.workspace / ".decomp" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        path = uploads / Path(name).name
        path.write_bytes(data)
        return path

    def import_split(self, console: consoles.Console, picked: Picked, *, toolchain: str | None = None,
                     flags: list[str] | None = None, log=print, reimport_slug: str | None = None) -> str:
        """ROM mode: split the program `console` read from the user's file, store every function as a target."""
        if reimport_slug is None:
            return self._import_split(console, picked, toolchain=toolchain, flags=flags, log=log)
        with self.progress.rebuilding(reimport_slug):
            return self._reimport_split(console, picked, toolchain=toolchain, flags=flags, log=log,
                                        reimport_slug=reimport_slug)

    def _reimport_split(self, console, picked, *, toolchain, flags, log, reimport_slug):
        proj = self._project(reimport_slug)
        cfg = json.loads(proj["config"])
        if cfg.get("kind") != "rom" or self.console(proj).id != console.id:
            raise NetworkError("choose a game for the same console as this project")
        if reimport_slug in self.rooms or reimport_slug in self.links:
            raise NetworkError("leave the shared room before reimporting this game")
        root = Path(proj["root"])
        if not root.is_dir():
            raise NetworkError("the project folder is missing; restore it before reimporting")
        log("Building the replacement analysis in a temporary folder; the current project is safe until the new split is ready.")
        stage = Path(tempfile.mkdtemp(prefix=f".{reimport_slug}-reimport-", dir=root.parent))
        try:
            return self._import_split(console, picked, toolchain=toolchain, flags=flags, log=log,
                                      reimport=proj, workdir=stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _import_split(self, console: consoles.Console, picked: Picked, *, toolchain: str | None = None,
                      flags: list[str] | None = None, log=print, reimport=None, workdir: Path | None = None) -> str:
        rom, info = picked.data, picked.info
        check_cancel = getattr(log, "check_cancel", lambda: None)
        check_cancel()
        if reimport is None:
            for p in self.store.all("SELECT slug, config FROM projects"):
                if json.loads(p["config"]).get("rom", {}).get("sha1") == info["sha1"]:
                    log(f"already imported as {p['slug']}")
                    return p["slug"]
            base = slugify(info["title"])
            slug, n = base, 2
            while self.store.one("SELECT 1 FROM projects WHERE slug=?", slug) or (self.workspace / slug).exists():
                slug, n = f"{base}-{n}", n + 1
            workdir = self.workspace / slug
            workdir.mkdir(parents=True)
        else:
            slug = reimport["slug"]
            if workdir is None:
                raise ValueError("reimport needs a temporary project folder")
        (workdir / picked.filename).write_bytes(rom)
        size = f"{info['size'] // (1024 * 1024)} MiB" if info["size"] >= 1 << 20 else f"{info['size'] // 1024} KiB"
        log(f"{console.name}: {info['title']} ({info['game_code'] or 'no game code'}, "
            f"{info['region']}), {size}, looks built with {info['compiler_guess']}")
        if picked.overlays:
            log(f"{len(picked.overlays)} overlay{'s' if len(picked.overlays) != 1 else ''} on the disc: "
                + ", ".join(f"{o[0]} at 0x{o[2]:08X}" for o in picked.overlays[:6]) + (", ..." if len(picked.overlays) > 6 else ""))
        # a program that kept its symbols names its functions, and its debug sections give their types (#3)
        debug = console.debug_info(rom, _HEADER_NAMES)
        if debug is not None:
            log(debug.summary())
        try:
            res = split.split_rom(workdir, picked.filename, console, picked, log=log,
                                  symbols=debug.symbol_addrs() if debug is not None and debug.named else None)
        except RomError:
            if debug is None or not debug.named:
                raise
            # the game's own names are a help, never the reason an import fails: split as if it had none
            log("splat refused the game's own names: splitting without them")
            shutil.rmtree(workdir / "asm", ignore_errors=True)
            res = split.split_rom(workdir, picked.filename, console, picked, log=log)
        if reimport is not None and not res.functions:
            raise NetworkError("the selected game produced no functions; the existing project was left unchanged")
        if reimport is not None:
            log(f"New split ready: {len(res.functions)} functions and {len(res.data)} data entries. Preparing to replace the old analysis.")
        if res.image and res.image != picked.filename:  # splat works on a flat image made from the program
            rom = (workdir / res.image).read_bytes()

        chosen = bool(toolchain or flags or reimport is not None)  # a reimport keeps the project's compiler
        toolchain = toolchain or console.default_compiler(info, res.compiler)
        default_flags = console.default_flags(toolchain, res.gp)
        cfg = {
            "kind": "rom", "platform": console.id, "big_endian": picked.big_endian, "gp": res.gp,
            "rom": {**info, "file": res.image or picked.filename, "program": picked.filename},
            "segments": [vars(s) for s in res.segments],
            "compiler": {"toolchain": toolchain, "flags": flags or default_flags, "timeout": 20},
            "legal": {"basis": "personal-copy", "license": "personal use only",
                      "statement": "imported by the owner of this copy of the game, who confirmed it was legally obtained",
                      "confirmed_at": time.time()},
            "context": {"file": None},
        }
        if reimport is not None:
            old_cfg = json.loads(reimport["config"])
            for key, value in old_cfg.items():
                if key not in {"kind", "platform", "big_endian", "gp", "rom", "segments", "legal"}:
                    cfg[key] = value
            cfg["compiler"] = old_cfg.get("compiler", cfg["compiler"])
            cfg.pop("export", None)
            cfg.pop("autopilot", None)
            cfg.pop("kernel_calls_named", None)
        header = BASE_HEADER.format(title=info["title"]) + (GP_NOTE if res.gp else "")
        header += self._debug_types(debug, cfg, workdir, log)
        symbols = SymbolTable()
        for name, addr in res.named.items():
            symbols.add(Symbol(name, addr, 0, "func"), reindex=False)
        for fn in res.functions:
            check_cancel()
            symbols.add(Symbol(fn.name, fn.vram, fn.size, "func"), reindex=False)
        rom_bytes, segs = rom, res.segments

        def read(addr, n):
            for seg in segs:
                if seg.contains(addr, n):
                    at = seg.rom_start + addr - seg.vram
                    return rom_bytes[at:at + n]
            return None

        data_rows = []
        for d in res.data:
            check_cancel()
            if d.name in symbols.by_name:
                continue
            symbols.add(Symbol(d.name, d.vram, d.size, d.kind), reindex=False)
            data_rows.append((d.name, d.vram, d.size, d.kind, data_preview(read(d.vram, min(d.size, 48)), d.kind) if d.kind != "bss" else ""))
        symbols.reindex_symbols()

        root = Path(reimport["root"]) if reimport is not None else workdir
        backup = None
        if reimport is not None:
            check_cancel()
            saved_progress = root / "progress"
            if saved_progress.is_dir():
                log("Copying saved progress into the replacement project.")
                shutil.copytree(saved_progress, workdir / "progress", dirs_exist_ok=True)
            log("Replacing project files and updating the function analysis.")
            backup = root.with_name(f".{root.name}-reimport-backup-{time.time_ns()}")
            root.replace(backup)
            try:
                workdir.replace(root)
            except OSError:
                backup.replace(root)
                raise
            workdir = root
        try:
            now = time.time()
            with self.store.tx() as db:
                if reimport is None:
                    pid = db.execute(
                        "INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                        (slug, info["title"], f"{info['game_code']} · {info['region']} · {len(res.functions)} functions",
                         console.arch, int(picked.big_endian), str(workdir), json.dumps(cfg))).lastrowid
                    old_functions, gone, old_names = {}, {}, {}
                else:
                    pid = reimport["id"]
                    rows = db.execute("SELECT * FROM functions WHERE project_id=?", (pid,)).fetchall()
                    # by address first: a function renamed since (rename_symbol, polishing) has its old name nowhere
                    # in the new split, and paired by name it lost its matched C
                    by_vram = {r["vram"]: r for r in rows if r["vram"] is not None}
                    by_name = {r["name"]: r for r in rows}
                    old_functions = {}
                    for fn in res.functions:
                        old = by_vram.get(fn.vram) or by_name.get(fn.name)
                        if old is not None and old["id"] not in {o["id"] for o in old_functions.values()}:
                            old_functions[fn.name] = old
                    paired = {o["id"] for o in old_functions.values()}
                    gone = {r["name"]: r for r in rows if r["id"] not in paired}
                    # the names agents gave (functions and data), by address: matched C calls them by those names
                    old_names = {r["address"]: r["name"] for r in db.execute(
                        "SELECT name, address FROM symbols WHERE project_id=?", (pid,)) if not AUTO_NAME.match(r["name"])}
                    db.execute("UPDATE projects SET description=?, arch=?, big_endian=?, config=? WHERE id=?",
                               (f"{info['game_code']} · {info['region']} · {len(res.functions)} functions",
                                console.arch, int(picked.big_endian), json.dumps(cfg), pid))
                    db.execute("DELETE FROM symbols WHERE project_id=?", (pid,))

                lost = []
                for name, old in gone.items():
                    for table in ("claims", "attempts", "twin_misses"):
                        db.execute(f"DELETE FROM {table} WHERE function_id=?", (old["id"],))
                    db.execute("UPDATE notes SET function_id=NULL, text=? || COALESCE(text, '') "
                               "WHERE function_id=?", (f"[{name} before reimport] ", old["id"]))
                    if old["status"] == "matched" and old["matched_source"]:  # its C stays readable, as a note
                        lost.append(name)
                        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,NULL,?,?)",
                                   (pid, f"[{name} before reimport: it matched with this C]\n{old['matched_source']}", now))
                    db.execute("DELETE FROM functions WHERE id=?", (old["id"],))
                if lost:
                    log(f"{len(lost)} matched function(s) aren't in the new split at their address: "
                        f"{', '.join(lost[:8])}; their C is kept in the project's notes")

                preserved = 0
                for fn in res.functions:
                    check_cancel()
                    target = match.target_from_split(fn, symbols.resolve, picked.big_endian, res.gp)
                    relocs = json.dumps(target.to_json())
                    known = debug.functions.get(fn.vram) if debug is not None else None
                    unit = debug.units.get(fn.vram, fn.segment) if debug is not None else fn.segment
                    old = old_functions.get(fn.name)
                    same_target = (old is not None and old["target"] == target.code
                                   and json.loads(old["relocs"] or "[]") == target.to_json())
                    asm = self._asm_source(fn)
                    meta = json.dumps({"debug": known}) if known else None
                    if old is None:
                        db.execute(
                            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,meta) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pid, fn.name, fn.size, len(fn.lines), target.code, relocs, asm, None, unit, fn.vram,
                             fn.rom, meta))
                    elif same_target:
                        db.execute(
                            "UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,unit=?,vram=?,rom_offset=?,meta=? "
                            "WHERE id=?",
                            (fn.size, len(fn.lines), target.code, relocs, asm, unit, fn.vram, fn.rom, meta, old["id"]))
                        preserved += old["status"] == "matched" and bool(old["matched_source"])
                    else:
                        for table in ("claims", "attempts", "twin_misses"):
                            db.execute(f"DELETE FROM {table} WHERE function_id=?", (old["id"],))
                        db.execute(
                            "UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,seed=NULL,unit=?,vram=?,"
                            "rom_offset=?,meta=?,status='open',best_score=NULL,best_source=NULL,best_agent_id=NULL,"
                            "matched_source=NULL,matched_agent_id=NULL,matched_at=NULL,attempts=0,abandons=0,"
                            "fingerprint=NULL,boost=0,readable=0,permuted=0,seed_key=NULL WHERE id=?",
                            (fn.size, len(fn.lines), target.code, relocs, asm, unit, fn.vram, fn.rom, meta, old["id"]))
                previews = {r[1]: r[4] for r in data_rows}
                for addr, name in old_names.items():
                    s = symbols.by_addr.get(addr)
                    if s is not None and s.name != name and name not in symbols.by_name:
                        symbols.rename(s.name, name)
                for s in symbols.by_name.values():
                    check_cancel()
                    preview = previews.get(s.address)
                    db.execute("INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(?,?,?,?,?,?)",
                               (pid, s.name, s.address, s.size, s.kind, preview))
                if reimport is None:
                    db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                               "VALUES(?,?,?,?,?,?,?)", (pid, 1, header, None, "basic types" + (
                                   " and the game's own, from its debug information" if DEBUG_TYPES_NOTE in header else ""),
                                                         now, ""))
                    event_kind = "import"
                else:
                    event_kind = "reimport"
                self._event(db, pid, None, event_kind, f"{len(res.functions)} functions from {info['title']}")
        except Exception:
            if backup is not None:
                failed_root = root.with_name(f".{root.name}-reimport-failed-{time.time_ns()}")
                root.replace(failed_root)
                backup.replace(root)
                shutil.rmtree(failed_root, ignore_errors=True)
            raise
        if reimport is not None:
            for cache in (self._roms, self._syms, self._sym_override, self._xrefs, self._asm_names,
                          self._ops, self._warmed, self._cmaps):
                cache.pop(pid, None)
            self._headers = {key: value for key, value in self._headers.items() if key[0] != pid}
            self._diffs.clear()
            self.surveys.pop(slug, None)
            self.twins.stop(slug)
            self.permuter.stop(slug)
        console.after_import(self, slug, chosen=chosen, log=log)
        if reimport is not None and backup is not None:
            try:
                shutil.rmtree(backup)
            except OSError as e:
                log(f"the old project files could not be removed; they remain at {backup}: {e}")
            log(f"reimport kept {preserved} matched functions whose targets were unchanged")
        log(f"ready: {len(res.functions)} functions to decompile")
        return slug

    def reimport_rom(self, slug: str, data: bytes | Path, *, confirmed: bool = False, name: str = "",
                     log=print) -> dict:
        """Replace a ROM project's split with a newly selected copy while retaining matching work."""
        if not confirmed:
            raise NetworkError("confirm that this is your own legally obtained copy of the game")
        log("Starting reimport: reading the selected game and checking its format.")
        proj = self._project(slug)
        if json.loads(proj["config"]).get("kind") != "rom":
            raise NetworkError("only ROM projects can be reimported")
        console = consoles.identify(data)
        picked = console.read_game(data, name)
        log("Game recognized; starting the replacement split.")
        self.import_split(console, picked, reimport_slug=slug, log=log)
        counts = self.store.one(
            "SELECT COUNT(*) AS functions, SUM(status='matched') AS matched FROM functions WHERE project_id=?",
            proj["id"])
        log(f"Reimport complete: {counts['functions']} functions; {counts['matched'] or 0} still match.")
        return {"slug": slug, "functions": counts["functions"], "matched": counts["matched"] or 0}

    def _debug_types(self, debug, cfg: dict, workdir: Path, log) -> str:
        """The game's own types from its debug information (consoles/ps2/debuginfo.py), for the first header, once
        the project's compiler accepts them: a header that doesn't compile would fail every attempt. Types it refuses
        stay in the project folder (debug_types.h) instead."""
        if debug is None or not debug.header:
            return ""
        from ...compiler import compile_source
        from ...project import compiler_config

        types = DEBUG_TYPES_NOTE + debug.header
        toolchain = cfg["compiler"]["toolchain"]
        if toolchains.installed(toolchain) is not None:
            base = BASE_HEADER.format(title="") + (GP_NOTE if cfg.get("gp") else "")
            if not compile_source(compiler_config(workdir, cfg), base + types, HEADER_CHECK).ok:
                (workdir / DEBUG_TYPES_FILE).write_text(debug.header, encoding="utf-8")
                log(f"the game's types don't compile with {consoles.compiler_name(toolchain)}: they're in "
                    f"{DEBUG_TYPES_FILE} in the project folder, not in the shared header")
                return ""
        return types

    def test_compilers(self, slug: str, compilers: list[str], sample: int = 24, log=print) -> dict[str, int]:
        """Which of `compilers` (the installed ones) builds this game: m2c drafts of small functions from all over
        it, compiled with each and judged like any attempt. The one that matches the most becomes the project's (a
        tie keeps the current one). What a program says about its compiler can mislead: GTA San Andreas keeps one
        library's "MW MIPS C Compiler" note, and its own code is EE-GCC 2.96's. Getters of 3-5 instructions count:
        m2c's drafts of bigger functions are GCC-style and CodeWarrior never matches them, but `return p->x;` is the
        same C for every compiler, and each fills the return's delay slot its own way. Functions in a range another
        compiler builds aren't asked. Returns compiler -> matches."""
        from concurrent.futures import ThreadPoolExecutor

        from ...compiler import compile_source
        from ...project import compiler_config

        proj = self._project(slug)
        cfg = json.loads(proj["config"])  # a copy of its own: the compiler may change below
        current = cfg["compiler"]["toolchain"]
        console = self.console(proj)
        tcs = [t for t in compilers if toolchains.installed(t) is not None
               and all(toolchains.installed(d) is not None for d in toolchains.get(t).requires)]
        rows = [r for r in self.store.all("SELECT * FROM functions WHERE project_id=? AND n_insns BETWEEN 3 AND 30 "
                                          "ORDER BY vram", proj["id"])
                if compiler_for(cfg, r["vram"])["toolchain"] == current]
        rows = rows[::max(1, len(rows) // sample)][:sample]  # spread over the whole game, not one library
        if len(tcs) < 2 or not rows or not seeds.available():
            return {}
        header = self._context(proj["id"])["header"]
        drafts = []
        for fn in rows:
            data_asm = "\n".join(self._jtbl_asm(proj, fn)) if "jtbl_" in (fn["asm"] or "") else None
            draft = seeds.generate_from_asm(self._asm_text(fn, proj), fn["name"], current, header, data_asm, valid=True)
            if draft:
                drafts.append((fn, f"{seeds.M2C_MACROS}\n{draft}\n"))
        log(f"testing {len(tcs)} compilers on {len(drafts)} small functions")

        def matches(job) -> bool:
            tc, (fn, source) = job
            settings = {"toolchain": tc, "flags": console.default_flags(tc, cfg.get("gp")), "timeout": 20}
            result = compile_source(compiler_config(Path(proj["root"]), {"compiler": settings}), header, source)
            if not result.ok:
                return False
            cand, verdict, _ = self._evaluate(fn, proj, result.obj)
            return cand is not None and (verdict if verdict is not None
                                         else scoring.diff(self._target_obj(fn, proj), cand).bytes_match)

        jobs = [(tc, d) for tc in tcs for d in drafts]
        with ThreadPoolExecutor(4) as pool:
            found = list(pool.map(matches, jobs))
        counts = {tc: sum(ok for (t, _), ok in zip(jobs, found) if t == tc) for tc in tcs}
        best = max(tcs, key=lambda t: (counts[t], t == current))
        log("compiler test: " + ", ".join(f"{consoles.compiler_name(t)} {counts[t]}" for t in tcs))
        if counts[best] >= 3 and counts[best] > counts.get(current, 0):  # 1-2: trivial code any compiler makes
            try:  # (run again on a project with work: its matched functions must still match)
                self.set_compiler(slug, best, guard=True)
            except NetworkError as e:
                log(f"{consoles.compiler_name(best)} matched more drafts, but stays unused: {e}")
                return counts
            log(f"{consoles.compiler_name(best)} builds this game's code ({counts[best]} of {len(drafts)} drafts match "
                f"byte for byte, {counts.get(current, 0)} with {consoles.compiler_name(current)}): using it")
        return counts

    @staticmethod
    def _asm_source(fn: split.RomFunction) -> str:
        out = [f"glabel {fn.name}"]
        for ln in fn.lines:
            if ln.vram in fn.labels and ln.vram != fn.vram:
                out.append(f".L{ln.vram:08X}:")
            out.append(f"/* {ln.rom:X} {ln.vram:08X} {ln.word:08X} */  {ln.mnemonic:<10} {ln.operands}".rstrip())
        out.append(f"endlabel {fn.name}")
        return "\n".join(out)

    @staticmethod
    def _parse_asm(name: str, text: str, segment: str) -> split.RomFunction:
        lines, labels = [], set()
        for raw in text.splitlines():
            m = split._INSN.match(raw)
            if m:
                rom, vram, word, mnem, ops = m.groups()
                lines.append(split.AsmLine(int(rom, 16), int(vram, 16), int(word, 16), mnem, ops))
                continue
            m = split._LABEL.match(raw)
            if m:
                labels.add(int(m.group(1).lstrip(".L"), 16))
        return split.RomFunction(name, lines[0].vram, lines[0].rom, segment, lines, Path(), labels)
