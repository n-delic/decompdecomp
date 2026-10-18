"""Importing a game: its console (consoles/: the connector) says what the file is and how its games are imported.
ROM mode's import is here: split the program (splat), make every function's target and the project's first
header, with what the console adds (more code to split, the game's own symbols, a test of which compiler built it).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from ... import consoles, scoring, seeds, toolchains
from ...network import NetworkError
from ...rom import Picked, RomError, match, split
from ...rom.symbols import Symbol, SymbolTable
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
                   confirmed: bool = False, name: str = "", log=print) -> str:
        """Import a game the user owns (ROM bytes, or a file such as a disc image): split it, store every function
        as a target. Returns the slug."""
        if not confirmed:
            raise NetworkError("confirm that this is your own legally obtained copy of the game")
        console = consoles.identify(data)
        return console.import_game(self, data, toolchain=toolchain, flags=flags, name=name, log=log)

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
                     flags: list[str] | None = None, log=print) -> str:
        """ROM mode: split the program `console` read from the user's file, store every function as a target."""
        rom, info = picked.data, picked.info
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
        if res.image and res.image != picked.filename:  # splat works on a flat image made from the program
            rom = (workdir / res.image).read_bytes()

        chosen = bool(toolchain or flags)  # the user's own choice stands
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
        header = BASE_HEADER.format(title=info["title"]) + (GP_NOTE if res.gp else "")
        header += self._debug_types(debug, cfg, workdir, log)
        symbols = SymbolTable()
        for name, addr in res.named.items():
            symbols.add(Symbol(name, addr, 0, "func"), reindex=False)
        for fn in res.functions:
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
            if d.name in symbols.by_name:
                continue
            symbols.add(Symbol(d.name, d.vram, d.size, d.kind), reindex=False)
            data_rows.append((d.name, d.vram, d.size, d.kind, data_preview(read(d.vram, min(d.size, 48)), d.kind) if d.kind != "bss" else ""))
        symbols.reindex_symbols()

        now = time.time()
        with self.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                             (slug, info["title"], f"{info['game_code']} · {info['region']} · {len(res.functions)} functions",
                              console.arch, int(picked.big_endian), str(workdir), json.dumps(cfg))).lastrowid
            for fn in res.functions:
                target = match.target_from_split(fn, symbols.resolve, picked.big_endian, res.gp)
                known = debug.functions.get(fn.vram) if debug is not None else None
                unit = debug.units.get(fn.vram, fn.segment) if debug is not None else fn.segment  # its source file
                db.execute(
                    "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,meta) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, fn.name, fn.size, len(fn.lines), target.code, json.dumps(target.to_json()),
                     self._asm_source(fn), None, unit, fn.vram, fn.rom, json.dumps({"debug": known}) if known else None))
            for s in symbols.by_name.values():
                preview = next((r[4] for r in data_rows if r[0] == s.name), None)
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(?,?,?,?,?,?)",
                           (pid, s.name, s.address, s.size, s.kind, preview))
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, 1, header, None, "basic types" + (
                           " and the game's own, from its debug information" if DEBUG_TYPES_NOTE in header else ""), now, ""))
            self._event(db, pid, None, "import", f"{len(res.functions)} functions from {info['title']}")
        console.after_import(self, slug, chosen=chosen, log=log)
        log(f"ready: {len(res.functions)} functions to decompile")
        return slug

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
        library's "MW MIPS C Compiler" note, and its own code is EE-GCC 2.96's. Returns compiler -> matches."""
        from concurrent.futures import ThreadPoolExecutor

        from ...compiler import compile_source
        from ...project import compiler_config

        proj = self._project(slug)
        cfg = json.loads(proj["config"])  # a copy of its own: the compiler may change below
        current = cfg["compiler"]["toolchain"]
        console = self.console(proj)
        tcs = [t for t in compilers if toolchains.installed(t) is not None
               and all(toolchains.installed(d) is not None for d in toolchains.get(t).requires)]
        rows = self.store.all("SELECT * FROM functions WHERE project_id=? AND n_insns BETWEEN 6 AND 30 ORDER BY vram",
                              proj["id"])
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
            cfg["compiler"] = {"toolchain": best, "flags": console.default_flags(best, cfg.get("gp")),
                               "timeout": 20}
            with self.store.tx() as db:
                db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
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
