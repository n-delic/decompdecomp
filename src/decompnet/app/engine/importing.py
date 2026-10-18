"""Importing a game: identify it, split it (splat), make every function's target and the project's first header,
and test which compiler built it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ... import scoring, seeds, toolchains
from ...consoles.ps2 import syscalls
from ...network import NetworkError
from ...rom import detect, match, split
from ...rom.symbols import Symbol, SymbolTable
from .common import (
    BASE_HEADER,
    DEFAULT_TOOLCHAIN,
    GP_NOTE,
    PLATFORM_COMPILERS,
    SHORT_NAMES,
    _default_flags,
    _preview,
    _slugify,
)


class ImportMixin:
    def import_rom(self, data: bytes | Path, *, toolchain: str | None = None, flags: list[str] | None = None,
                   confirmed: bool = False, name: str = "", log=print) -> str:
        """Import a game the user owns (ROM bytes, or a file such as a disc image): split it, store every function
        as a target. Returns the slug."""
        if not confirmed:
            raise NetworkError("confirm that this is your own legally obtained copy of the game")
        project_file = self._community_layout(data, name)
        if project_file is not None:  # a dtk-template or ds-decomp project, worked on like a fork
            compiler = toolchain if toolchain and toolchain.startswith(("GC/", "Wii/", "DS/", "MSVC/")) else None
            return self.forks.create_scratch(project_file, compiler=compiler, log=log)
        picked = detect.identify(data, name)
        rom, info = picked.data, picked.info
        for p in self.store.all("SELECT slug, config FROM projects"):
            if json.loads(p["config"]).get("rom", {}).get("sha1") == info["sha1"]:
                log(f"already imported as {p['slug']}")
                return p["slug"]
        base = _slugify(info["title"])
        slug, n = base, 2
        while self.store.one("SELECT 1 FROM projects WHERE slug=?", slug) or (self.workspace / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        workdir = self.workspace / slug
        workdir.mkdir(parents=True)
        (workdir / picked.filename).write_bytes(rom)
        size = f"{info['size'] // (1024 * 1024)} MiB" if info["size"] >= 1 << 20 else f"{info['size'] // 1024} KiB"
        log(f"{detect.PLATFORMS[picked.platform]}: {info['title']} ({info['game_code'] or 'no game code'}, "
            f"{info['region']}), {size}, looks built with {info['compiler_guess']}")
        if picked.platform == "gba":
            return self._import_gba(picked, slug, workdir, toolchain, flags, log)
        if picked.overlays:
            log(f"{len(picked.overlays)} overlay{'s' if len(picked.overlays) != 1 else ''} on the disc: "
                + ", ".join(f"{o[0]} at 0x{o[2]:08X}" for o in picked.overlays[:6]) + (", ..." if len(picked.overlays) > 6 else ""))
        res = split.split_rom(workdir, picked.filename, log=log, overlays=picked.overlays)
        if res.image and res.image != picked.filename:  # splat works on a flat image made from the program
            rom = (workdir / res.image).read_bytes()

        guess = info["compiler_guess"] if picked.platform == "ps2" else res.compiler  # PS2: what the program says
        # the user's own choice stands; the test picks among the GCCs (m2c's drafts are GCC-style: CodeWarrior never
        # matches them, so the test can't speak for it)
        test_compilers = picked.platform == "ps2" and not toolchain and info["compiler_guess"].startswith("EEGCC")
        toolchain = toolchain or DEFAULT_TOOLCHAIN.get(guess, DEFAULT_TOOLCHAIN[info["compiler_guess"]])
        default_flags = _default_flags(toolchain, picked.platform, res.gp)
        cfg = {
            "kind": "rom", "platform": picked.platform, "big_endian": picked.big_endian, "gp": res.gp,
            "rom": {**info, "file": res.image or picked.filename, "program": picked.filename},
            "segments": [vars(s) for s in res.segments],
            "compiler": {"toolchain": toolchain, "flags": flags or default_flags, "timeout": 20},
            "legal": {"basis": "personal-copy", "license": "personal use only",
                      "statement": "imported by the owner of this copy of the game, who confirmed it was legally obtained",
                      "confirmed_at": time.time()},
            "context": {"file": None},
        }
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
            data_rows.append((d.name, d.vram, d.size, d.kind, _preview(read(d.vram, min(d.size, 48)), d.kind) if d.kind != "bss" else ""))
        symbols._reindex()

        now = time.time()
        with self.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                             (slug, info["title"], f"{info['game_code']} · {info['region']} · {len(res.functions)} functions",
                              "mips", int(picked.big_endian), str(workdir), json.dumps(cfg))).lastrowid
            for fn in res.functions:
                target = match.target_from_split(fn, symbols.resolve, picked.big_endian, res.gp)
                db.execute(
                    "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, fn.name, fn.size, len(fn.lines), target.code, json.dumps(target.to_json()),
                     self._asm_source(fn), None, fn.segment, fn.vram, fn.rom))
            for s in symbols.by_name.values():
                preview = next((r[4] for r in data_rows if r[0] == s.name), None)
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(?,?,?,?,?,?)",
                           (pid, s.name, s.address, s.size, s.kind, preview))
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, 1, BASE_HEADER.format(title=info["title"]) + (GP_NOTE if res.gp else ""),
                                                  None, "basic types", now, ""))
            self._event(db, pid, None, "import", f"{len(res.functions)} functions from {info['title']}")
        if test_compilers and not flags:
            self.test_compilers(slug, log=log)
        self.name_kernel_calls(slug, log=log)
        log(f"ready: {len(res.functions)} functions to decompile")
        return slug

    def name_kernel_calls(self, slug: str, log=print) -> int:
        """PS2: the kernel's call stubs get their names (consoles/ps2/syscalls.py), once per project: at import, and
        for projects imported before this existed, when the app starts. Returns how many were named."""
        proj = self._project(slug)
        cfg = self.cfg(proj)
        if cfg.get("kind") != "rom" or cfg.get("platform") != "ps2" or cfg.get("kernel_calls_named") or self._link(slug):
            return 0  # (a room's guest: the host names them)
        rows = self.store.all("SELECT name, target FROM functions WHERE project_id=? ORDER BY vram", proj["id"])
        pairs = syscalls.stub_names([(r["name"], r["target"]) for r in rows], set(self.symbols(proj["id"]).by_name))
        if pairs:
            agent, _ = self.local_agent("autopilot")
            said = self.rename_symbols(agent, slug, pairs, f"name the {len(pairs)} kernel calls (syscall stubs)")
            named = said.startswith("✅")
            log(f"named {len(pairs)} kernel calls (FlushCache, CreateThread, ...)" if named
                else f"the kernel calls keep their names: {said}")
        # once either way: a check that fails (it compiles every matched function again) would fail at every start
        self._update_cfg(slug, lambda c: c.__setitem__("kernel_calls_named", True))
        return len(pairs) if not pairs or named else 0

    def test_compilers(self, slug: str, sample: int = 24, log=print) -> dict[str, int]:
        """Which of the platform's installed compilers builds this game: m2c drafts of small functions from all over
        it, compiled with each and judged like any attempt. The one that matches the most becomes the project's (a
        tie keeps the current one). What a program says about its compiler can mislead: GTA San Andreas keeps one
        library's "MW MIPS C Compiler" note, and its own code is EE-GCC 2.96's. Returns compiler -> matches."""
        from concurrent.futures import ThreadPoolExecutor

        from ...compiler import compile_source
        from ...project import compiler_config

        proj = self._project(slug)
        cfg = self.cfg(proj)
        current = cfg["compiler"]["toolchain"]
        tcs = [t for t in PLATFORM_COMPILERS.get(cfg.get("platform"), []) if toolchains.installed(t) is not None
               and all(toolchains.installed(d) is not None for d in toolchains.get(t).requires)
               and not t.startswith("mwcps2")]  # drafts are GCC-style: see import_rom
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
            settings = {"toolchain": tc, "flags": _default_flags(tc, cfg["platform"], cfg.get("gp")), "timeout": 20}
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
        log("compiler test: " + ", ".join(f"{SHORT_NAMES.get(t, t)} {counts[t]}" for t in tcs))
        if counts[best] >= 3 and counts[best] > counts.get(current, 0):  # 1-2: trivial code any compiler makes
            cfg["compiler"] = {"toolchain": best, "flags": _default_flags(best, cfg["platform"], cfg.get("gp")),
                               "timeout": 20}
            with self.store.tx() as db:
                db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
            log(f"{SHORT_NAMES.get(best, best)} builds this game's code ({counts[best]} of {len(drafts)} drafts match "
                f"byte for byte, {counts.get(current, 0)} with {SHORT_NAMES.get(current, current)}): using it")
        return counts

    def _import_gba(self, picked, slug: str, workdir: Path, toolchain: str | None, flags: list[str] | None, log) -> str:
        """Game Boy Advance: functions found by luvdis (plus a pass for code nothing calls), judged by the ARM matcher."""
        from ...rom import gba

        info = picked.info
        fns = gba.split(workdir / picked.filename, workdir, log=log)
        toolchain = toolchain or "agbcc"
        tc = toolchains.get(toolchain)
        cfg = {
            "kind": "rom", "platform": "gba", "big_endian": False, "gp": 0, "arch": "thumb",
            "rom": {**info, "file": picked.filename, "program": picked.filename},
            "segments": [{"name": "rom", "rom_start": 0, "rom_end": info["size"], "vram": gba.BASE}],
            "compiler": {"toolchain": toolchain, "flags": flags or tc.flags, "timeout": 20},
            "legal": {"basis": "personal-copy", "license": "personal use only",
                      "statement": "imported by the owner of this copy of the game, who confirmed it was legally obtained",
                      "confirmed_at": time.time()},
            "context": {"file": None},
        }
        now = time.time()
        with self.store.tx() as db:
            pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                             (slug, info["title"], f"{info['game_code']} · {info['region']} · {len(fns)} functions",
                              "arm", 0, str(workdir), json.dumps(cfg))).lastrowid
            names: set[str] = set()
            for f in fns:
                name = f.asm_only or f.name  # known hand-written helpers keep their real name and stay assembly
                if name in names:  # a game can link a helper twice (Spyro Orange has two of _call_via_r3)
                    name = f"{name}_{f.vram:08X}"
                names.add(name)
                db.execute(
                    "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, name, len(f.code), f.n_insns, f.code, json.dumps([list(r) for r in f.refs or []]),
                     f.asm.replace(f.name, name), None, "rom", f.vram, f.rom, "asm" if f.asm_only else "open"))
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind,preview) VALUES(?,?,?,?,?,?)",
                           (pid, name, f.vram, len(f.code), "func", None))
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (pid, 1, BASE_HEADER.format(title=info["title"]), None, "basic types", now, ""))
            self._event(db, pid, None, "import", f"{len(fns)} functions from {info['title']}")
        log(f"ready: {len(fns)} functions to decompile")
        return slug

    def _community_layout(self, data: bytes | Path, name: str = "") -> Path | None:
        """The file, when the game starts as a community-layout project: a GameCube/Wii program (DOL) or disc image
        (dtk-template), a DS ROM (ds-decomp), or a Windows program (.exe, see scratch_pe). Bytes are saved to a file
        first."""
        from ...forks import scratch_dsd, scratch_dtk, scratch_pe

        head = Path(data).open("rb").read(0x1000) if isinstance(data, (str, Path)) else bytes(data[:0x1000])
        small = Path(data).read_bytes() if isinstance(data, (str, Path)) and Path(data).stat().st_size < 64 << 20 else data
        nds, pe = scratch_dsd.is_nds(head), scratch_pe.is_pe(head)
        if not nds and not pe and scratch_dtk.disc_kind(head) is None and not (isinstance(small, (bytes, bytearray))
                                                                              and scratch_dtk.is_dol(small)):
            return None
        if isinstance(data, (str, Path)):
            return Path(data)
        uploads = self.workspace / ".decomp" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        path = uploads / (Path(name).name or ("game.nds" if nds else "game.exe" if pe else "main.dol"
                                              if scratch_dtk.is_dol(data) else "game.iso"))
        path.write_bytes(data)
        return path

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
