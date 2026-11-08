"""Which compiler builds which part of a game: the project's compiler and its address ranges, agents'
suggestions and try_compiler, and the compiler map with the judge's survey (compilermap.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from contextlib import closing, nullcontext

from ... import arch, compiler, compilermap, consoles, toolchains
from ...compile_sessions import batched
from ...network import Agent, NetworkError
from ...project import compiler_for
from .common import parse_ranges

# Flag search (search_flags): the flags of each compiler family worth trying, at most MAX_FLAG_VARIANTS compiles
FLAG_FAMILIES = (("ido", "ido"), ("mwcps2", "mwcps2"), ("mwccpsp", "mwcps2"), ("ee-gcc", "ee"), ("gcc", "gcc"),
                 ("psp-gcc", "gcc"), ("pspsnc", "snc"))
FLAG_LEVELS = {"gcc": ("-O1", "-O2", "-O3", "-Os"), "ee": ("-O1", "-O2", "-O3", "-Os"),
               "ido": ("-O1", "-O2", "-O3", "-g"), "mwcps2": ("-O1", "-O2", "-O3", "-O4"),
               "snc": ("-O0", "-O1", "-O2", "-O4")}  # SNC: -O3 builds as -O2 and -O5 as -O4; it has no -Os
FLAG_EXTRAS = {"gcc": ("-fno-strength-reduce", "-fno-schedule-insns"),
               "ee": ("-fno-strength-reduce", "-fno-schedule-insns", "-fno-common"),
               "ido": (), "mwcps2": (), "snc": ("-Xsched=0",)}  # SNC's instruction scheduling off
SMALL_DATA_SIZES = (0, 8)
MAX_FLAG_VARIANTS = 12
FLAGS_SHOWN = 6


class CompilerMixin:
    # Settings: auto-adjust compiler. A suggested compiler is applied without asking once it's proven (a function's
    # C matches with it) or three agents agree on it (see suggest)
    auto_compiler = False

    AGREE = 3

    SURVEY_MIN_INSNS = 12  # a shorter function comes out the same from several compilers: it proves nothing
    # The compiler answer's own checking: the survey runs again by itself at most this often (it skips C it tried, so
    # a run where little changed is quick)
    SURVEY_AGAIN_SECONDS = 3600
    ANSWER_STRETCHES = 12  # stretches that look built by another compiler, listed in the answer

    def compiler_map(self, proj, fresh: bool = False) -> compilermap.CompilerMap | None:
        """Which compiler built each part of a MIPS game: matches, the judge's confirmations and the habits that rule
        compilers out. Rebuilt when a match, a range or a confirmation changes it, one build at a time per project;
        meanwhile the last map answers, unless `fresh` (wait for the one that's current). GTA San Andreas: each match
        made the map stale, and every waiting brief and map request rebuilt it at once, 600-870 ms each.
        None: not MIPS code."""
        if not self.is_rom(proj) or arch.COMPILER_MAP not in self.console(proj).features:
            return None
        cached = self._cmaps.get(proj["id"])
        if cached and cached[0] == self._cmap_key(proj):
            return cached[1]
        building = self._cmap_builds.setdefault(proj["id"], threading.Lock())
        if cached and not fresh:
            if building.acquire(blocking=False):  # nobody builds it yet: in the background
                threading.Thread(target=self._rebuild_cmap, args=(proj["slug"], building), daemon=True,
                                 name=f"compiler-map-{proj['slug']}").start()
            return cached[1]
        with building:  # the first map, or a fresh one: build it, or wait for the build under way
            return self._build_cmap(proj)

    def _cmap_key(self, proj) -> tuple:
        """What the compiler map is built from, summed up: matches, the compilers and ranges, confirmations."""
        cfg = self.cfg(proj)
        done = self.project_store(proj['id']).one("SELECT COUNT(*) AS n, COALESCE(SUM(status='matched'), 0) AS m, COALESCE(MAX(matched_at), 0) "
                              "AS t FROM functions WHERE project_id=?", proj["id"])
        confirmed = (cfg.get("compiler_survey") or {}).get("confirmed", {})
        return tuple(done), json.dumps([cfg.get("compiler"), cfg.get("compiler_ranges"),cfg.get('function_compilers')], sort_keys=True), len(confirmed)

    def _rebuild_cmap(self, slug: str, building: threading.Lock) -> None:
        try:
            with self.progress.view() if hasattr(self,'progress') else nullcontext():
                self._build_cmap(self._project(slug))  # its settings now, not those of the request that asked
        except Exception:  # noqa: BLE001  the last map keeps answering
            logging.getLogger("decompdecomp").exception("the compiler map failed")
        finally:
            building.release()

    def _build_cmap(self, proj) -> compilermap.CompilerMap:
        key = self._cmap_key(proj)
        cached = self._cmaps.get(proj["id"])
        if cached and cached[0] == key:  # built while this one waited
            return cached[1]
        cfg = self.cfg(proj)
        confirmed = (cfg.get("compiler_survey") or {}).get("confirmed", {})

        def label(c: dict) -> compilermap.Label:
            return c["toolchain"], tuple(c["flags"])
        rows = self.project_store(proj['id']).all("SELECT name,vram,rom_offset,size,target,status FROM functions WHERE project_id=? AND target IS NOT NULL",
                              proj["id"])
        built = compilermap.build(
            [(r["vram"], r["size"], r["target"], label(compiler_for(cfg, r["vram"], r)), r["status"] == "matched",
              (lambda c: (c[0], tuple(c[1])) if c else None)(confirmed.get(str(r["vram"])))) for r in rows],
            bool(cfg.get("big_endian", True)))
        self._cmaps[proj["id"]] = (key, built)
        return built

    def _short(self, lab: compilermap.Label) -> str:
        return f"{consoles.compiler_name(lab[0])} {' '.join(lab[1])}".strip()

    def compiler_map_text(self, slug: str, survey: bool = False, check_ranges: bool = False) -> str:
        """The map for agents: where it differs from the compiler the game builds with today, and why. A project
        whose source files each have a compiler (PC, DS) has it over its files (app/filecompilers.py)."""
        if self.filecompilers.applies(slug):
            return self.filecompilers.text(slug, survey, check_ranges)
        if self.fork_slug(slug):
            raise NetworkError("this project's own build sets its compilers: there's no compiler map to read")
        proj = self._project(slug)
        cmap = self.compiler_map(proj, fresh=True)  # an agent asked: what the evidence says now
        if cmap is None:
            raise NetworkError("the compiler map reads MIPS code (PlayStation 2, PlayStation, N64) for now")
        started = ""
        if survey:
            started = self.start_survey(slug)["state"] + ". "
        if check_ranges:
            started += (f"Checking {self.rangechecks.audit(proj)} applied range(s) again, with the evidence their "
                        f"neighbours already give (in the background). ")
        cfg = self.cfg(proj)
        syms = self.symbols(proj["id"])
        lines = [f"{started}The game builds with {self._short((cfg['compiler']['toolchain'], tuple(cfg['compiler']['flags'])))}"
                 f" and {len(cfg.get('compiler_ranges', []))} address ranges with others.",
                 "Evidence, matched or confirmed by the judge, per compiler: "
                 + ", ".join(f"{t} {s}".strip() + f": {n}" for (t, s), n in cmap.examples.most_common())]
        if cmap.never:
            lines.append("Habits that rule a compiler out (its matched code never shows them): "
                         + "; ".join(f"{t} {s}".strip() + ": " + ", ".join(h) for (t, s), h in cmap.never.items()))
        differ = [r for r in cmap.runs if r.differs()]
        if differ:
            lines.append("Where the code looks built by another compiler than today's (runs of neighbouring functions):")
            for r in differ[:30]:
                today = self._short(r.assigned.most_common(1)[0][0])
                lines.append(f"- 0x{r.start:08X}-0x{r.end:08X}: {self._short(r.label)}, {r.functions} functions "
                             f"({r.confirmed} confirmed by the judge, {r.matched} matched); today {today}")
        else:
            lines.append("The code agrees with the compilers the game builds it with today.")
        if cmap.contradicted:
            names = ", ".join(syms.label(a) for a in sorted(cmap.contradicted)[:12])
            lines.append(f"{len(cmap.contradicted)} functions show a habit their compiler never does: {names}"
                         + (", ..." if len(cmap.contradicted) > 12 else "") + ". try_compiler them with another one.")
        lines += self.rangechecks.text(proj)
        state = self.surveys.get(slug) or {}
        last = (cfg.get("compiler_survey") or {})
        lines.append("The judge's survey (agents' C built with each compiler the project uses): "
                     + (f"running, {state.get('tried', 0)} tried, {state.get('confirmed', 0)} confirmed" if state.get("state") == "running"
                        else f"{len(last.get('confirmed', {}))} confirmed in {len(last.get('tried', {}))} functions tried"
                        if last else "not run yet")
                     + ". compiler_map(survey=true) runs it in the background; runs with confirmations become suggestions, "
                       "checked against every matched function before they apply.")
        return "\n".join(lines)

    def start_survey(self, slug: str, limit: int | None = None) -> dict:
        """The survey in the background (one per project)."""
        state = self.surveys.get(slug)
        if state and state["state"] == "running":
            return state
        state = self.surveys[slug] = {"state": "running", "tried": 0, "confirmed": 0, "suggested": [], "error": None,
                                      "t": time.time()}

        def run():
            try:
                state.update(self.survey_compilers(slug, limit=limit, state=state))
                state["state"] = "done"
            except Exception as e:  # noqa: BLE001  a background job says why it stopped
                logging.getLogger("decompdecomp").exception("compiler survey failed")
                state.update(state="error", error=str(e))
        threading.Thread(target=run, daemon=True, name=f"survey-{slug}").start()
        return state

    def _comparison_candidates(self, proj, all_installed=True):
        cfg = self.cfg(proj)
        usable={}
        def ready(tid):
            if tid not in usable:
                usable[tid]=bool(toolchains.installed(tid) and toolchains.get(tid).supported and not toolchains.runtime_error(tid))
            return usable[tid]
        candidates = sorted({(cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))}
                            | {(r["toolchain"], tuple(r["flags"])) for r in cfg.get("compiler_ranges", [])}
                            | {(r['toolchain'],tuple(r['flags'])) for r in cfg.get('function_compilers',{}).values()})
        if all_installed:
            # Only this console's installed, usable compilers: never download tools or call an AI provider.
            candidates = sorted({(c.id, tuple(consoles.default_flags(cfg, c.id)))
                                 for c in consoles.of(cfg).compilers
                                 if c.automatic and ready(c.id)} | set(candidates))
            candidates = [(tc, flags) for tc, flags in candidates if ready(tc)]
        # Debug line labels can change old GCC assemblers' delay-slot scheduling even at the same -O level.
        candidates = sorted({(tc, tuple(variant)) for tc, flags in candidates
                             for variant in self.debug_flag_variants(tc, list(flags))})
        if all_installed:
            candidates = sorted({(tc, tuple(debug)) for tc, flags in candidates
                                 for variant in self.flag_variants(tc, list(flags))
                                 for debug in self.debug_flag_variants(tc, variant)})
        default = (cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))
        return sorted(candidates, key=lambda item: item != default)

    def _used_comparison_candidates(self, proj):
        """Exact profiles assigned to functions today, without generated flag variants."""
        cfg = self.cfg(proj)
        used = {(p['toolchain'], tuple(p['flags'])) for p in self.compiler_profile_usage(proj)}
        default = (cfg['compiler']['toolchain'], tuple(cfg['compiler']['flags']))
        usable={tid:bool(toolchains.installed(tid) and toolchains.get(tid).supported
                         and not toolchains.runtime_error(tid)) for tid in sorted({p[0] for p in used})}
        return sorted((p for p in used if usable[p[0]]),key=lambda p:(p!=default,p))

    def _comparison_inputs(self, proj, candidates):
        """Read and encode project-wide inputs once per eligibility pass."""
        encode = lambda value: json.dumps(value, sort_keys=True).encode()
        return {'contexts': [list(r) for r in self.project_store(proj['id']).all(
                    "SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY unit", proj['id'])],
                'symbols': encode([list(r) for r in self.project_store(proj['id']).all(
                    "SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name", proj['id'])]),
                'tools': {tid: toolchains.installed(tid) for tid in sorted({tc for tc, _ in candidates})},
                'units': {}, 'options': {}, 'cfg': self.cfg(proj)}

    def _comparison_digest(self, proj, fn, source, candidates, shared=None):
        shared = shared if shared is not None else self._comparison_inputs(proj, candidates)
        encode = lambda value: json.dumps(value, sort_keys=True).encode()
        unit = fn['unit']
        if unit not in shared['units']:
            shared['units'][unit] = encode([r for r in shared['contexts'] if r[0] in ('', unit)])
        key = tuple((tc, tuple(flags)) for tc, flags in candidates)
        if key not in shared['options']:
            shared['options'][key] = (encode(candidates), encode([[tid, shared['tools'][tid]]
                                                     for tid in sorted({tc for tc, _ in candidates})]))
        options, tools = shared['options'][key]
        cfg = shared['cfg']
        # Stream the same canonical JSON as before. Existing completed comparisons stay valid;
        # shared headers/symbols/options are serialized once rather than once per function.
        fields = [encode(source), options, shared['units'][unit], shared['symbols'], tools,
                  encode((fn['target'] or b'').hex()), encode(fn['relocs']), encode(fn['vram']),
                  encode(cfg.get('gp')), encode(cfg.get('big_endian')),
                  encode(compiler_for(cfg, fn['vram'], fn)), encode(cfg['compiler'])]
        digest = hashlib.sha256()
        digest.update(b'[')
        for index, field in enumerate(fields):
            if index:
                digest.update(b', ')
            digest.update(field)
        digest.update(b']')
        return digest.hexdigest()

    def _comparison_source(self, fn):
        return fn["matched_source"] if fn["status"] == "matched" and fn["matched_source"] else self._source_of(fn)

    def compiler_profile_usage(self, proj):
        """Current assignments, not historical alternatives that happened to compile."""
        cfg = self.cfg(proj)
        profiles = {}
        fallback={}
        for own in (cfg.get('function_compilers') or {}).values():
            fallback.setdefault((own.get('function'),own.get('vram')),own)
        for fn in self.project_store(proj['id']).all("SELECT name,unit,vram,rom_offset,status FROM functions WHERE project_id=?", proj["id"]):
            row=dict(fn)
            if row.get('rom_offset') is None:
                own=fallback.get((row.get('name'),row['vram']))
                profile=({**cfg['compiler'],'toolchain':own['toolchain'],'flags':own['flags']} if own else compiler_for(cfg,row['vram']))
            else:profile=compiler_for(cfg,row['vram'],row)
            key = (profile["toolchain"], tuple(profile["flags"]))
            if key not in profiles:
                profiles[key] = {"toolchain": key[0], "flags": list(key[1]), "functions": 0, "matched": 0}
            profiles[key]["functions"] += 1
            profiles[key]["matched"] += int(fn["status"] == "matched")
        return list(profiles.values())

    def compiler_test_result(self, proj, fn):
        row = self.project_store(proj['id']).one("SELECT * FROM compiler_tests WHERE function_id=?", fn["id"])
        if row is None:
            return None
        comparison = json.loads(row["result"])
        candidates = self._used_comparison_candidates(proj) if comparison.get("used_profiles_only") else self._comparison_candidates(proj)
        if comparison.get("flags_only"):
            own = compiler_for(self.cfg(proj), fn["vram"], fn)
            candidates = [p for p in candidates if p[0] == own["toolchain"]]
        digest = self._comparison_digest(proj, fn, self._comparison_source(fn), candidates)
        default = self.cfg(proj)["compiler"]
        for result in comparison.get("ranked", []):
            result["default"] = result["toolchain"] == default["toolchain"] and result["flags"] == default["flags"]
        return {"completed_at": row["completed_at"], "stale": digest != row["input_digest"], "comparison": comparison}

    def run_compiler_comparison(self, slug, log=print, function="", untested_only=True, flags_only=False, auto_apply=False, used_profiles_only=False):
        lock = self._compiler_comparison_locks.setdefault(slug, threading.Lock())
        if not lock.acquire(blocking=False):
            raise NetworkError("A compiler comparison is already running for this project. Wait for it to finish.")
        try:
            check = getattr(log, 'check_cancel', None)
            with compiler.cancellable(check):
                if check:
                    check()
                return self.survey_compilers(slug, log=log, all_installed=True, function=function,
                                             untested_only=untested_only and not function, flags_only=flags_only, auto_apply=auto_apply, used_profiles_only=used_profiles_only)
        finally:
            lock.release()

    @batched
    def survey_compilers(self, slug: str, log=lambda line: None, limit: int | None = None, state: dict | None = None,
                         all_installed: bool = False, function: str = "", untested_only: bool = False, flags_only: bool = False,
                         auto_apply: bool = False, used_profiles_only: bool = False) -> dict:
        # Saved-file watching owns synchronization. A comparison uses current DB inputs and
        # rejudges before applying; loading a project here must not restore/reverify its files.
        with self.progress.view() if hasattr(self,'progress') else nullcontext():
            if auto_apply and all_installed and not function:
                from ..compiler_apply_queue import Queue
                queue = Queue(self, log)
                try:
                    return self._survey_compilers(slug,queue,limit,state,all_installed,function,untested_only,
                                                  flags_only,auto_apply,used_profiles_only,apply_queue=queue)
                finally:queue.close()
            return self._survey_compilers(slug,log,limit,state,all_installed,function,untested_only,
                                          flags_only,auto_apply,used_profiles_only)

    def _survey_compilers(self, slug: str, log=lambda line: None, limit: int | None = None, state: dict | None = None,
                         all_installed: bool = False, function: str = "", untested_only: bool = False, flags_only: bool = False,
                         auto_apply: bool = False, used_profiles_only: bool = False, apply_queue=None) -> dict:
        """The judge's part of the compiler map. Agents' C for functions that don't match yet, built with each other
        compiler the project uses: C that matches the game with another compiler (and not with today's) confirms that
        compiler for its function. Contradicted functions first, then the closest attempts; C already tried is
        skipped. Then each run of the map with confirmations becomes a suggestion, checked like an agent's."""
        if type(auto_apply) is not bool or auto_apply and not all_installed:
            raise NetworkError("Auto-apply requires an installed-compiler comparison and a boolean option.")
        if not isinstance(function, str):
            raise NetworkError("Choose a function by name.")
        started=time.monotonic()
        check_cancel=getattr(log,'check_cancel',lambda:None)
        report=getattr(log,'progress',lambda value:None)
        def preparing_phase(label):
            check_cancel()
            report({'phase':'preparation','stage':label,'elapsed':time.monotonic()-started})
        def phase_done(label,began):
            log(f'Compiler preparation · {label}: {time.monotonic()-began:.3f}s')
        preparing_phase('Reading project settings')
        began=time.monotonic()
        self._rom_only(slug, "the compiler survey")
        proj = self._project(slug)
        phase_done('Project settings',began)
        if not self.is_rom(proj) or arch.COMPILER_MAP not in self.console(proj).features:
            raise NetworkError("the compiler map reads MIPS code (PlayStation 2, PlayStation, N64) for now")
        # A bulk comparison can judge pairs without building a compiler map first.
        cached=self._cmaps.get(proj['id'])
        cmap=cached[1] if all_installed and cached else (None if all_installed else self.compiler_map(proj))
        cfg = self.cfg(proj)
        if function and not all_installed:
            raise NetworkError("Single-function comparison needs installed-compiler mode.")
        preparing_phase('Checking installed compiler profiles')
        began=time.monotonic()
        candidates = self._used_comparison_candidates(proj) if used_profiles_only else self._comparison_candidates(proj, all_installed)
        phase_done('Compiler profiles',began)
        if all_installed and not candidates:
            raise NetworkError("Install at least one usable compiler in Compilers & tools before testing.")
        if all_installed and not function:
            scope = "all eligible" if limit is None else f"up to {limit}"
            log(f"Testing {len(candidates)} compiler/flag combinations per function on {scope} existing unmatched C attempts. This uses the same flag search as a single-function comparison and may take longer.")
        old = cfg.get("compiler_survey") or {}
        tried, confirmed = ({}, {}) if all_installed else (dict(old.get("tried", {})), dict(old.get("confirmed", {})))
        preparing_phase('Loading function queue')
        began=time.monotonic()
        if function:
            fn=self._function(function,slug)
            if not self._comparison_source(fn):
                raise NetworkError("This function has no C attempt yet. Create a draft or attempt first.")
            rows=[fn]
            log(f"{function}: testing all {len(candidates)} supported compiler/flag combinations (not an exhaustive search of every possible flag).")
        else:
            fields='id,vram,best_score' if all_installed else '*'
            rows=self.project_store(proj['id']).all(f"SELECT {fields} FROM functions WHERE project_id=? AND status NOT IN ('matched', 'asm') AND n_insns>=?",
                                proj['id'],0 if all_installed else self.SURVEY_MIN_INSNS)
        contradicted=cmap.contradicted if cmap else set()
        rows.sort(key=lambda f:(f['vram'] not in contradicted,f['best_score'] if f['best_score'] is not None else 1<<30))
        phase_done('Function queue',began)
        state = state if state is not None else {}
        comparison = None
        check_cancel = getattr(log, "check_cancel", lambda: None)
        improvements = []
        results = []
        n = found = skipped = applied_count = 0
        available_candidates = candidates
        if used_profiles_only:
            log("Only exact compiler profiles currently assigned to project functions; no generated flag variations.")
        if flags_only:
            log("Flag variations only: each function uses its currently selected compiler; other compilers are skipped.")
        # Discover eligibility in small batches and yield each batch immediately.
        # Large sources/assembly are fetched only for that batch, not the project.
        planned_digests={}
        total=len(rows) if limit is None else min(limit,len(rows));deferred=max(0,len(rows)-total)
        if all_installed:
            metadata=rows
            preparing_phase('Reading shared headers and symbols')
            began=time.monotonic()
            shared=self._comparison_inputs(proj,available_candidates)
            previous_tests={r['function_id']:r['input_digest'] for r in self.project_store(proj['id']).all(
                "SELECT t.function_id,t.input_digest FROM compiler_tests t JOIN functions f ON f.id=t.function_id "
                "WHERE f.project_id=?",proj['id'])} if untested_only else {}
            phase_done('Shared inputs and history',began)
            total=None;deferred=0
            def eligible_rows():
                nonlocal total,deferred,skipped
                scanned=eligible=0
                def preparing():
                    report({'phase':'eligibility','checked':scanned,'scan_total':len(metadata),
                            'eligible':eligible,'skipped':skipped,'completed':n})
                preparing()
                for offset in range(0,len(metadata),25):
                    check_cancel()
                    batch=metadata[offset:offset+25]
                    if function:
                        full=batch
                    else:
                        loaded={r['id']:r for r in self.project_store(proj['id']).all(
                            'SELECT id,project_id,name,unit,vram,rom_offset,status,best_score,best_source,matched_source,meta,target,relocs '
                            'FROM functions WHERE project_id=? AND id IN (SELECT value FROM json_each(?))',
                            proj['id'],json.dumps([r['id'] for r in batch]))}
                        full=[loaded[r['id']] for r in batch if r['id'] in loaded]
                    pending=[]
                    for fn in full:
                        check_cancel()
                        scanned+=1
                        if not function and fn['status'] in ('matched','asm'):continue
                        source=self._comparison_source(fn)
                        if not source:continue
                        own=compiler_for(cfg,fn['vram'],fn)
                        options=([p for p in available_candidates if p[0]==own['toolchain']] if flags_only else available_candidates)
                        if not options:
                            skipped+=1;continue
                        digest=self._comparison_digest(proj,fn,source,options,shared)
                        if untested_only and previous_tests.get(fn['id'])==digest:
                            skipped+=1;continue
                        planned_digests[fn['id']]=digest
                        pending.append(fn);eligible+=1
                    scanned=min(offset+len(batch),len(metadata))
                    if scanned==len(metadata):
                        total=eligible if limit is None else min(limit,eligible);deferred=max(0,eligible-total)
                    preparing()
                    yield from pending
                total=eligible if limit is None else min(limit,eligible);deferred=max(0,eligible-total)
            rows=eligible_rows()
        for fn in rows:
            check_cancel()
            if limit is not None and n >= limit:
                if all_installed:continue  # Count deferred work after the first results.
                break
            discovered = fn
            if all_installed and not function:
                # Assembly and seeds are needed only for work actually selected to compile.
                fn = self.project_store(proj['id']).one('SELECT * FROM functions WHERE project_id=? AND id=?', proj['id'], fn['id'])
                if fn is None or fn['status'] in ('matched', 'asm'):
                    continue
            source = self._comparison_source(fn)
            if not source:
                continue
            own = compiler_for(cfg, fn["vram"], fn)
            candidates = ([p for p in available_candidates if p[0] == own["toolchain"]]
                          if flags_only else available_candidates)
            if not candidates:
                skipped += 1
                continue
            input_digest = planned_digests.get(fn["id"])
            if all_installed and any(fn[key] != discovered[key] for key in discovered.keys()):
                input_digest = self._comparison_digest(proj, fn, source, candidates, shared)
            digest = hashlib.sha1(json.dumps([source, candidates]).encode()).hexdigest()[:16]
            if tried.get(str(fn["vram"])) == digest:
                continue
            n += 1
            own = compiler_for(cfg, fn["vram"], fn)
            today = (own["toolchain"], tuple(own["flags"]))
            if all_installed:
                matches = []
                baseline = 100.0 if fn["status"] == "matched" else (max(0.0, 100 * (1 - fn["best_score"] / max(1, fn["n_insns"] * 100)))
                            if fn["best_score"] is not None else None)
                best = None
                best_profile = None
                ranked = []
                report = getattr(log, 'progress', lambda value: None)
                progress = {"function": fn["name"], "tested": 0, "combinations": len(candidates),
                            "before": baseline, "best": best, "profile": best_profile, "exact_matches_current": 0,
                            "total": total, "remaining": total - (n - 1) if total is not None else None, "deferred": deferred,
                            "completed": n - 1, "improved": len(improvements), "exact": found,
                            "skipped": skipped, "results": list(results), "auto_apply": auto_apply, "applied_count": applied_count}
                report(progress)
                if n==1:log(f'Compiler preparation · first compile ready: {time.monotonic()-started:.3f}s')
                from ..compiler_profiles import check_profiles
                with closing(check_profiles(self, proj, fn, candidates, source, check_cancel)) as checks:
                    for index, ((tc, flags), (ok, diff, _)) in enumerate(checks, 1):
                        check_cancel()
                        percent = 100.0 if ok else diff.percent if diff is not None else None
                        if all_installed:
                            ranked.append({"toolchain": tc, "flags": list(flags), "percent": percent,
                                           "exact": ok, "current": (tc, flags) == today,
                                           "default": tc == cfg["compiler"]["toolchain"] and list(flags) == cfg["compiler"]["flags"]})
                        if percent is not None and (best is None or percent > best):
                            best, best_profile = percent, [tc, list(flags)]
                            if not function and (baseline is None or best > baseline + 0.001):
                                before_text = f"{baseline:.2f}%" if baseline is not None else "no previous score"
                                log(f"{fn['name']}: better settings found: {before_text} → {best:.2f}% "
                                    f"with {self._short((tc, flags))}. Settings unchanged.")
                        if function:
                            score_text = f"{percent:.2f}%" if percent is not None else "could not compile or compare"
                            log(f"{index}/{len(candidates)} · {self._short((tc, flags))}: {score_text}"
                                + (" — exact match" if ok else "")
                                + (" (current settings)" if (tc, flags) == today else ""))
                        if ok:
                            matches.append([tc, list(flags)])
                            log(f"{fn['name']}: MATCH {self._short((tc, flags))}"
                                + (" (current settings)" if (tc, flags) == today else ""))
                        progress = {**progress, "tested": index, "best": best, "profile": best_profile, "exact_matches_current": len(matches)}
                        report(progress)
                        if index % 10 == 0:
                            best_text = f"{best:.2f}%" if best is not None else "no usable result"
                            before_text = f"{baseline:.2f}%" if baseline is not None else "no previous score"
                            log(f"{fn['name']}: {index}/{len(candidates)} combinations tested; "
                                f"best {best_text} (saved {before_text}); {len(matches)} exact matches.")
                improved = best is not None and (baseline is None or best > baseline + 0.001)
                if improved:
                    improvements.append({"function": fn["name"], "before": baseline, "best": best,
                                         "profile": best_profile})
                ranked.sort(key=lambda r: (not (r["exact"] and r["default"]), r["percent"] is None, -(r["percent"] or 0), r["toolchain"], r["flags"]))
                completed = {"before": baseline, "best": best, "profile": best_profile,
                             "tested": len(candidates), "exact_matches": len(matches),
                             "improved": improved, "ranked": ranked, "flags_only": flags_only, "used_profiles_only": used_profiles_only}
                check_cancel()
                with self.project_store(proj['id']).tx() as db:
                    db.execute("INSERT OR REPLACE INTO compiler_tests VALUES(?,?,?,?)",
                               (fn["id"], input_digest, time.time(), json.dumps(completed)))
                applied = False
                result_row = {"function": fn["name"], "before": baseline, "best": best,
                              "profile": best_profile, "improved": improved, "exact": bool(matches),
                              "applied": False, "apply_error": ""}
                results.append(result_row)
                if auto_apply and best_profile and (improved or matches):
                    def apply(fn=fn, source=source, candidates=candidates, input_digest=input_digest,
                              best_profile=best_profile, completed=completed, result_row=result_row):
                        nonlocal applied_count
                        applied, apply_error = False, ""
                        check_cancel()
                        log(f"{fn['name']}: rechecking and applying best settings.")
                        try:
                            current = self._function(fn["name"], slug)
                            current_proj = self._project(slug)
                            if (self._comparison_source(current) != source or
                                    self._comparison_digest(current_proj, current, source, candidates) != input_digest):
                                raise NetworkError("Code or build inputs changed during comparison; review a fresh result before applying.")
                            check_cancel()
                            self.apply_function_compiler(slug, fn["name"], best_profile[0], best_profile[1])
                            applied = True
                            applied_count += 1
                            log(f"{fn['name']}: best settings applied and rechecked.")
                            fresh = self._function(fn["name"], slug)
                            input_digest = self._comparison_digest(self._project(slug), fresh, self._comparison_source(fresh), candidates)
                        except NetworkError as error:
                            apply_error = str(error)
                            log(f"{fn['name']}: auto-apply skipped: {error}")
                        completed.update(applied=applied, apply_error=apply_error)
                        result_row.update(applied=applied, apply_error=apply_error)
                        with self.project_store(proj['id']).tx() as db:
                            db.execute("INSERT OR REPLACE INTO compiler_tests VALUES(?,?,?,?)",
                                       (fn["id"], input_digest, time.time(), json.dumps(completed)))
                    if apply_queue:
                        apply_queue.submit(fn['name'], apply)
                    else:
                        report({**progress, 'phase':'applying', 'stage':'Rechecking and applying best settings'})
                        apply()
                        applied = result_row['applied']
                if function:
                    comparison = completed
                if matches:
                    confirmed[str(fn["vram"])] = matches
                    found += 1
                elif function:
                    log(f"{fn['name']}: no exact matches among the tested combinations.")
                report({"function": fn["name"], "tested": len(candidates), "combinations": len(candidates), "exact_matches_current": len(matches),
                        "before": baseline, "best": best, "profile": best_profile,
                        "total": total, "remaining": total - n if total is not None else None, "deferred": deferred,
                        "completed": n, "improved": len(improvements), "exact": found, "skipped": skipped,
                        "results": list(results), "auto_apply": auto_apply, "applied": applied, "applied_count": applied_count})
                state.update(tried=n, confirmed=found)
                if n % 25 == 0:
                    log(f"{n} functions tried, {found} with exact matches")
                continue
            for tc, flags in candidates:
                if (tc, flags) == today or not self._judge_with(proj, fn, tc, list(flags), source)[0]:
                    continue
                if self._judge_with(proj, fn, today[0], list(today[1]), source)[0]:
                    break  # it matches with today's compiler too: no evidence (and an agent's next compile matches it)
                confirmed[str(fn["vram"])] = [tc, list(flags)]
                found += 1
                log(f"{fn['name']}: its C matches the game with {self._short((tc, flags))}, not with {self._short(today)}")
                break
            tried[str(fn["vram"])] = digest
            state.update(tried=n, confirmed=found)
            if n % 25 == 0:
                log(f"{n} functions tried, {found} confirmed")
        if apply_queue:apply_queue.finish()
        if all_installed:
            # Auto-apply is explicit for this run; the compiler-map agreement preference is separate.
            log(f"Finished: {n} functions tested; {len(improvements)} have better tested settings; "
                f"{found} have exact matches. {applied_count} function profiles applied and rechecked.")
            if not n:
                log("All eligible saved attempts already have up-to-date comparisons." if skipped else
                    "No eligible unmatched C attempts yet. Run the free matching pass or agents first.")
            return {"tried": n, "skipped": skipped, "total": total, "remaining": max(0, total - n),
                    "deferred": deferred, "confirmed": found, "matches": confirmed, "comparison": comparison,
                    "improvements": sorted(improvements, key=lambda r: -r["best"]), "results": results,
                    "auto_apply": auto_apply, "applied_count": applied_count}
        self._update_cfg(slug, lambda c: c.__setitem__("compiler_survey", {"tried": tried, "confirmed": confirmed,
                                                                           "t": time.time()}))
        suggested = self._suggest_from_map(proj, confirmed)
        for line in suggested:
            log(line)
        if found:
            with self.project_store(proj['id']).tx() as db:
                self._event(db, proj["id"], None, "compiler",
                            f"the compiler survey: {found} function(s) match with another compiler than today's")
        return {"tried": n, "confirmed": found, "suggested": suggested}

    def _suggest_from_map(self, proj, confirmed: dict) -> list[str]:
        """Each run of the map that the judge says another compiler built, as a suggestion from the autopilot (applied
        by itself only where the user lets proven compiler changes apply; checked against matched functions)."""
        proj = self._project(proj["slug"])  # its settings as the survey left them
        cmap = self.compiler_map(proj, fresh=True)  # with the survey's confirmations
        agent, _ = self.local_agent("autopilot")
        out = []
        for run in cmap.proposals(self.RANGE_PROOF_BYTES, self.RANGE_PROOF_FUNCTIONS):
            proof = next((int(a) for a, lab in sorted(confirmed.items())
                          if run.start <= int(a) < run.end and compilermap.kind((lab[0], tuple(lab[1]))) == compilermap.kind(run.label)), None)
            if proof is None:
                continue
            fn = self.project_store(proj['id']).one("SELECT * FROM functions WHERE project_id=? AND vram=?", proj["id"], proof)
            source = self._comparison_source(fn) if fn else None
            if not source:
                continue
            tc, flags = confirmed[str(proof)][0], tuple(confirmed[str(proof)][1])
            text = (f"The compiler map: {run.functions} functions at 0x{run.start:X}-0x{run.end:X} look built by "
                    f"{self._short((tc, flags))}; {run.confirmed} of them match the game with it and not with today's "
                    f"compiler ({fn['name']}'s C is the check).")
            try:
                said = self.suggest(agent, proj["slug"], text, compiler=tc, flags=" ".join(flags), function=fn["name"],
                                    ranges=f"0x{run.start:X}-0x{run.end:X}", source=source)
            except NetworkError as e:
                said = f"not suggested: {e}"
            out.append(f"0x{run.start:X}-0x{run.end:X} {self._short((tc, flags))}: {said}")
        return out

    def _compiler_brief(self, fn, proj) -> list[str]:
        """What get_function says when the map disagrees with the compiler a function is built with."""
        try:
            cmap = self.compiler_map(proj)
        except Exception:  # never stands between an agent and the function
            logging.getLogger("decompdecomp").exception("the compiler map failed")
            return []
        if cmap is None or fn["status"] == "matched":
            return []
        own = compiler_for(self.cfg(proj), fn["vram"], fn)
        today = (own["toolchain"], tuple(own["flags"]))
        looks = cmap.of.get(fn["vram"])
        lines = []
        if looks and compilermap.kind(looks) != compilermap.kind(today):
            lines.append(f"The compiler map says this part of the game looks built by {self._short(looks)}, not "
                         f"{self._short(today)}: if it won't match, try_compiler(\"{fn['name']}\", \"{looks[0]}\", "
                         f"\"{' '.join(looks[1])}\").")
        if fn["vram"] in cmap.contradicted:
            habits = ", ".join(cmap.never.get(compilermap.kind(today), []))
            lines.append(f"Its code shows a habit {self._short(today)} never has here ({habits}): it was probably "
                         f"built by another compiler. compiler_map() shows which compilers this game uses.")
        for c in self.rangechecks.checks_on(proj, fn["vram"]):
            lines.append(self.rangechecks.task_text(c, fn["name"]))
        return ["", "### Compiler"] + lines if lines else []

    def suggest(self, agent: Agent, slug: str, text: str, compiler: str = "", flags: str = "",
                function: str | None = None, ranges: str = "", source: str = "") -> str:
        """A change to the project's setup an agent can't make itself (the compiler, its flags, anything), for the user
        to apply or dismiss. With a compiler and a function whose C is ready, it's checked first: that C, built with the
        suggested compiler, matches the game or doesn't (GTA San Andreas: agents said EE-GCC 2.96, then 2.95.3). The
        same compiler and flags suggested again counts as another agent agreeing. `ranges` ("0x10BFD0-0x10CD40, ..."):
        the compiler built those address ranges only (a game built by several compilers)."""
        text = " ".join(text.split())[:1000]
        spans = parse_ranges(ranges)
        if spans and not compiler:
            raise NetworkError("say which compiler built those ranges (compiler=...)")
        if not text:
            raise NetworkError("say what should change, and what you saw that says so")
        proj = self._project(slug)
        cfg = self.cfg(proj)
        tc, flag_list, check = None, [], None
        if compiler and self.filecompilers.applies(slug):  # its files each have one: a range is checked on files
            return self.filecompilers.suggest(agent, slug, text, compiler, function, ranges)
        if compiler:
            if self.fork_slug(slug):
                raise NetworkError("a fork builds with its project's own compilers: suggest the change in words only")
            tc = self._toolchain_named(cfg, compiler)
            flag_list = flags.split() or consoles.default_flags(cfg, tc)
            if not spans and not function and (tc, flag_list) == (cfg["compiler"]["toolchain"], cfg["compiler"]["flags"]):
                raise NetworkError("that's already the project's compiler and flags")
            if function:
                check = self._check_compiler(proj, tc, flag_list, function, source)
                if re.search(r"does not match|doesn't compile", check):
                    raise NetworkError(f"not suggested: {check.removeprefix('checked: ')}. Test compilers with "
                                       "try_compiler first; suggest the one its C matches with")

        from .. import function_profile_suggestions
        bundle = function_profile_suggestions.capture(self, proj, function, spans, source) if tc and function else None

        def change(cfg):
            items = cfg.setdefault("suggestions", [])
            same = next((s for s in items if tc and s["status"] == "open" and s["toolchain"] == tc
                         and s["flags"] == flag_list and s.get("ranges", []) == spans
                         and (s.get("function_match") or {}).get("function") == (bundle or {}).get("function")), None)
            if same is None:
                same = {"id": max((s["id"] for s in items), default=0) + 1, "text": text, "toolchain": tc,
                        "flags": flag_list, "ranges": spans, "agents": [], "check": None, "status": "open",
                        "t": time.time()}
                items.append(same)
            if bundle:
                same["function_match"] = bundle
            same["agents"] = sorted({*same["agents"], agent.name})
            if check and (not same["check"] or "matches the game" in check):
                same["check"] = check
            found.append(same)
        found: list[dict] = []
        self._update_cfg(slug, change)
        s = found[0]
        note = f" ({check})" if check else ""
        if bundle:
            if not self.auto_adjusts(self._project(slug)):
                return f"Suggested function-only compiler bundle for {function}; automatic compiler changes are off. Apply it to recheck and save the source/header/profile together.{note}"
            try:
                function_profile_suggestions.apply(self, agent, self._project(slug), bundle, tc, flag_list)
                self._update_cfg(slug, lambda cfg: next(item for item in cfg['suggestions'] if item['id']==s['id']).__setitem__('status','applied'))
            except NetworkError as error:
                return f"Suggested function-only bundle, not applied: {error}. No broader compiler range was changed.{note}"
            return f"MATCH: {function}'s exact source, header and compiler flags were saved together for this function only. Neighbouring settings are unchanged."
        if spans and tc:  # a hypothesis: its neighbours decide (rangecheck.py), not its size
            return f"Suggested.{note} " + self.rangechecks.propose(proj, s, function, tc, flag_list, spans, source)
        if tc and self.auto_adjusts(proj) and ("matches the game" in (s["check"] or "") or len(s["agents"]) >= self.AGREE):
            try:
                self.resolve_suggestion(slug, s["id"], apply=True, guard=True)
            except NetworkError as e:  # it would break matched code: it waits for the user
                return f"Suggested, not applied by itself: {e}.{note}"
            where = f"{', '.join(f'0x{a:X}-0x{b:X}' for a, b in spans)} build" if spans else "the project builds"
            return (f"Applied (the user lets agreed compiler changes apply themselves): {where} with "
                    f"{consoles.compiler_name(tc)} {' '.join(flag_list)} from now on.{note}")
        return f"Suggested: the user sees it on the project page and can apply it.{note}"

    def auto_adjusts(self, proj) -> bool:
        """Whether this project's proven compiler suggestions apply by themselves: the project's own setting (a game
        whose agents keep finding quirks, like Ridge Racer V, wants it off while another wants it on), else the app's
        older global one, which projects without their own keep."""
        own = self.cfg(proj).get("auto_compiler")
        return self.auto_compiler if own is None else bool(own)

    def compiler_answer(self, slug: str, check_now: bool = False, *, display_only=False) -> dict:
        with self.progress.view() if hasattr(self,'progress') else nullcontext():
            return self._compiler_answer(slug,check_now,display_only=display_only)

    def _compiler_answer(self, slug: str, check_now: bool = False, *, display_only=False) -> dict:
        """Which compiler built the game, as one answer for the GUI: the project's compiler, the parts built with
        another one (each with what checking it said), and the stretches whose code looks like another compiler's
        that nothing applies yet. The app does the checking itself: the judge's survey runs in the background when
        it's older than SURVEY_AGAIN_SECONDS, and every applied range gets looked at again once. "Survey again" and
        "Check the applied ranges" were buttons nobody could say when to press, the owner and their author included.
        `check_now`: both at once, whatever their age."""
        if self.fork_slug(slug):  # a project whose source files each have a compiler (PC, DS): the files are its parts
            files = self.filecompilers
            if files.applies(slug):  # the survey and the checks, as below, over its files (app/filecompilers.py)
                last = self.cfg(self._project(slug)).get("file_survey") or {}
                if not display_only and len(files.candidates(slug)) > 1 and (check_now or time.time() - (last.get("t") or 0) >
                                                         self.SURVEY_AGAIN_SECONDS):
                    files.start_survey(slug)
                if check_now:
                    files.recheck(slug)
            return self.forks.compiler_answer(slug)
        self._rom_only(slug, "the compiler answer")
        proj = self._project(slug)
        cfg = self.cfg(proj)
        main = (cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))
        ranges = cfg.get("compiler_ranges", [])
        cmap = self.compiler_map(proj)  # None: not MIPS code (no map, no survey)
        state = self.surveys.get(slug) or {}
        if cmap is not None:
            last = cfg.get("compiler_survey") or {}
            several = len({main} | {(r["toolchain"], tuple(r["flags"])) for r in ranges}) > 1  # (one compiler: nothing to try)
            if not display_only and several and state.get("state") != "running" and (check_now or time.time() - (last.get("t") or 0) > self.SURVEY_AGAIN_SECONDS):
                state = self.start_survey(slug)
            audited = {(tuple(map(tuple, c["spans"])), c["toolchain"]) for c in cfg.get("range_checks", []) if c.get("audit")}
            if not display_only and ranges and (check_now or any((((r["start"], r["end"]),), r["toolchain"]) not in audited for r in ranges)):
                self.rangechecks.audit(proj)
                cfg = self.cfg(self._project(slug))
        checks = [dict(c) for c in cfg.get("range_checks", [])]
        if any(c["status"] == "waiting" for c in checks):
            rows = self.rangechecks._rows(proj)
            for c in checks:
                if c["status"] == "waiting":
                    c["said"] = self.rangechecks.waiting_reason(c, rows)
        audits = {tuple(map(tuple, c["spans"])): c for c in checks if c.get("audit")}
        parts = []
        function_rows = self.rangechecks._rows(proj)
        for r in ranges:
            c = audits.get(((r["start"], r["end"]),)) or {}
            parts.append({"start": r["start"], "end": r["end"], "compiler": consoles.compiler_name(r["toolchain"]),
                          "flags": " ".join(r["flags"]), "flag_list": list(r["flags"]), "toolchain": r["toolchain"],
                          "default": (r["toolchain"], tuple(r["flags"])) == main,
                          "family": toolchains.compiler_family(r["toolchain"]) or r["toolchain"],
                          "functions": sum(r["start"] <= f["vram"] < r["end"] for f in function_rows),
                          "function_list": [{k: f[k] for k in ("name", "vram", "status")} for f in function_rows if r["start"] <= f["vram"] < r["end"]],
                          "check": c.get("status", ""), "said": c.get("said", "")})
        elsewhere = sorted((r for r in (cmap.runs if cmap else []) if r.differs()), key=lambda r: -r.functions)
        def uses_default(fn):
            own = compiler_for(cfg, fn["vram"], fn)
            return (own["toolchain"], tuple(own["flags"])) == main
        # the compiler's name and its flags apart: "built with KMC GCC 2.7.2" reads at a glance, the flags are details
        return {"compiler": consoles.compiler_name(main[0]), "flags": " ".join(main[1]), "parts": parts,
                "default_function_list": [dict(f) for f in function_rows if uses_default(f)],
                "default_functions": sum(uses_default(f) for f in function_rows), "mips": cmap is not None,
                "elsewhere": [{"start": r.start, "end": r.end, "compiler": self._short(r.label), "functions": r.functions,
                               "confirmed": r.confirmed} for r in elsewhere[:self.ANSWER_STRETCHES]],
                "elsewhere_count": len(elsewhere),
                "checking": state.get("state") == "running" or any(c["status"] == "checking" for c in checks),
                "survey": {"state": state.get("state"), "tried": len((cfg.get("compiler_survey") or {}).get("tried", {})),
                           "confirmed": len((cfg.get("compiler_survey") or {}).get("confirmed", {})),
                           "running_tried": state.get("tried", 0), "t": (cfg.get("compiler_survey") or {}).get("t")},
                "checks": [{"id": c["id"], "audit": bool(c.get("audit")), "status": c["status"], "said": c.get("said") or "",
                            "ranges": c["spans"], "compiler": self._short((c["toolchain"], tuple(c["flags"])))} for c in checks]}

    def install_compilers(self, slug: str, accept_license: bool, retest: bool = True, log=print) -> dict:
        """Every compiler of the project's console the app installs and this computer lacks (a proprietary one only
        with the user's consent), then, unless the user picked the compiler (`retest`), the compiler test again with
        all of them: the import's test could only try what was installed, and a guess from the program alone can
        miss (a game linked by CodeWarrior was GCC's code). Returns what was installed, left out, and the test's
        counts."""
        proj = self._project(slug)
        console = self.console(proj)
        installed, left_out = [], []
        for tid in console.installable_compilers():
            if toolchains.installed(tid) is not None:
                continue
            if toolchains.get(tid).proprietary and not accept_license:
                left_out.append(tid)
                continue
            log(f"installing {consoles.compiler_name(tid)}")
            toolchains.install(tid, accept_license=accept_license, log=log)
            installed.append(tid)
        if left_out:
            log(f"left out, as proprietary: {', '.join(consoles.compiler_name(t) for t in left_out)}")
        counts = self.test_compilers(slug, console.installable_compilers(), log=log) if retest and self.is_rom(proj) else {}
        return {"installed": installed, "left_out": left_out, "test": counts}

    def set_auto_adjust(self, slug: str, on: bool) -> None:
        self._update_cfg(slug, lambda cfg: cfg.__setitem__("auto_compiler", bool(on)))

    def _toolchain_named(self, cfg: dict, compiler: str) -> str:
        names = [c.id for c in consoles.of(cfg).compilers]
        tc = next((t for t in names if compiler.strip().lower() in (t, consoles.compiler_name(t).lower())), None)
        if tc is None:
            raise NetworkError(f"no compiler {compiler!r} for this game; its platform's: {', '.join(names)}")
        return tc

    def _check_compiler(self, proj, toolchain: str, flags: list[str], function: str, source: str = "") -> str:
        """Whether a function's C (`source`, else its best attempt, else its latest) matches the game when built with
        this compiler."""
        fn = self._function(function, proj["slug"])
        source = source if source.strip() else self._source_of(fn)
        if not source:
            return f"not checked: {fn['name']} has no attempt yet"
        ok, d, _ = self._judge_with(proj, fn, toolchain, flags, source)
        if d is None:
            return f"checked: {fn['name']}'s C doesn't compile with it"
        return f"checked: {fn['name']}'s C {'matches the game byte for byte' if ok else 'does not match'} with it"

    RANGE_PROOF_BYTES, RANGE_PROOF_FUNCTIONS = 256, 3

    def try_compiler(self, agent: Agent, slug: str, function: str, compiler: str, flags: str = "",
                     source: str = "", keep_match: bool = True, structured: bool = False) -> str | dict:
        """A function's C (`source`, else its best attempt) built with another compiler and flags, judged like an
        attempt but recorded nowhere: to work out which compiler built a part of the game before suggesting it (on GTA
        San Andreas an agent tried compilers by suggesting them: 128 of its 149 suggestions were disproved)."""
        def outcome(text, exact=False, score=None):
            return {'text':text,'exact':bool(exact),'score':score} if structured else text
        if self.fork_slug(slug):  # where the project lets each file have its own compiler (DS), the fork engine's
            return self.forks.try_compiler(agent, slug, function, compiler, flags, source, keep_match=keep_match)
        proj = self._project(slug)
        cfg = self.cfg(proj)
        fn = self._function(function, slug)
        current = compiler.strip().lower() == "current"
        own = compiler_for(cfg,fn["vram"],fn)
        tc = own["toolchain"] if current else self._toolchain_named(cfg, compiler)
        flag_list = flags.split() or (own.get("flags",[]) if current else consoles.default_flags(cfg, tc))
        source = source if source.strip() else self._source_of(fn)
        if not source:
            raise NetworkError(f"{fn['name']} has no attempt yet: pass the C as source")
        ok, d, problem = self._judge_with(proj, fn, tc, flag_list, source)
        what = f"{consoles.compiler_name(tc)} {' '.join(flag_list)}"
        if d is None:
            return outcome(f"{what}: doesn't compile:\n{problem.strip()[-1500:]}")
        if ok:
            self.rangechecks.tried(proj, fn, tc, flag_list, source, True)
            return outcome(f"{what}: MATCHES the game byte for byte. Suggest it: suggest(compiler=\"{tc}\", flags=\"{' '.join(flag_list)}\", "
                    f"function=\"{fn['name']}\", source=<this exact C>). A function-specific suggestion rechecks and saves only this function; broader ranges require separate neighbour evidence.", exact=True, score=0)
        return outcome(f"{what}: score {d.score} ({d.percent:.1f}%), not a match\n{d.render(context=1, max_rows=40)}", score=max(1,d.score))

    @staticmethod
    def debug_flag_variants(toolchain: str, base: list[str]) -> list[list[str]]:
        """Old GCC's debug labels affect assembler scheduling: try no debug, -g and -g3 independently."""
        if not toolchain.startswith(("gcc", "ee-gcc")) and toolchains.compiler_family(toolchain) != "gcc":
            return [base]
        plain = [f for f in base if not re.fullmatch(r"-g[0-3]?", f)]
        out = [base]
        for variant in (plain, [*plain, "-g"], [*plain, "-g3"]):
            if variant not in out:
                out.append(variant)
        return out

    def flag_variants(self, toolchain: str, base: list[str], more: tuple[str, ...] = ()) -> list[list[str]]:
        """The flags worth trying for a function on this compiler: its optimisation levels, small data on and off
        (MIPS GCC's -G; CodeWarrior's -sdatathreshold), and the few options that change the shape of code most.
        Each variant changes the project's flags in one place, so what matches says which flag it was."""
        family = toolchains.compiler_family(toolchain) or next((f for prefix, f in FLAG_FAMILIES if toolchain.startswith(prefix)), "gcc")
        levels, extras = FLAG_LEVELS[family], FLAG_EXTRAS[family] + tuple(more)
        current = next((f for f in base if f in levels), None)
        out = [base]
        for level in levels:
            if level != current:
                out.append([level if f == current else f for f in base] if current else [*base, level])
        if family in ("gcc", "ee", "snc"):
            small = next((f for f in base if f.startswith("-G") and f[2:].isdigit()), None)
            for g in SMALL_DATA_SIZES:
                if small and small != f"-G{g}":
                    out.append([f"-G{g}" if f == small else f for f in base])
        elif family == "mwcps2" and "-sdatathreshold" in base:
            at = base.index("-sdatathreshold") + 1
            for g in SMALL_DATA_SIZES:
                if base[at] != str(g):
                    out.append(base[:at] + [str(g)] + base[at + 1:])
        out += [v for v in CompilerMixin.debug_flag_variants(toolchain, base) if v not in out]
        out += [[*base, extra] for extra in extras if extra not in base]
        return out[:MAX_FLAG_VARIANTS]

    def search_flags(self, agent: Agent, slug: str, function: str, compiler: str = "", source: str = "") -> str:
        """A function's C (its best attempt, or `source`) built with the variants of its compiler's flags
        (flag_variants), each judged like an attempt and recorded nowhere: when the C looks right but won't match,
        the flags are often what's wrong, and agents tried them one compile at a time."""
        if self.fork_slug(slug):  # where the project lets each file have its own flags (PC), the fork engine's
            return self.forks.search_flags(agent, slug, function, compiler, source)
        proj = self._project(slug)
        cfg = self.cfg(proj)
        fn = self._function(function, slug)
        own = compiler_for(cfg, fn["vram"], fn)
        tc = self._toolchain_named(cfg, compiler) if compiler else own["toolchain"]
        base = list(own["flags"]) if tc == own["toolchain"] else consoles.default_flags(cfg, tc)
        source = source if source.strip() else self._source_of(fn)
        if not source:
            raise NetworkError(f"{fn['name']} has no attempt yet: pass the C as source")
        results = []
        for flags in self.flag_variants(tc, base, consoles.of(cfg).flag_extras):
            ok, d, _ = self._judge_with(proj, fn, tc, flags, source)
            if d is not None:
                results.append((0 if ok else max(1, d.score), flags))
        if not results:
            return f"{consoles.compiler_name(tc)}: the C doesn't compile with any of its flag variants"
        results.sort(key=lambda r: r[0])
        lines = [f"- score {score:>5}  {' '.join(flags)}" + ("   ← the project's flags" if flags == base else "")
                 for score, flags in results[:FLAGS_SHOWN]]
        best_score, best = results[0]
        head = f"{consoles.compiler_name(tc)}, {len(results)} flag variants for {fn['name']} (lower is better):\n"
        if best_score == 0 and best != base:
            if tc == own["toolchain"]:
                self.rangechecks.tried(proj, fn, tc, best, source, True)
            return (head + "\n".join(lines) + f"\nIt MATCHES with {' '.join(best)}. Suggest it: suggest(compiler=\"{tc}\", "
                    f"flags=\"{' '.join(best)}\", function=\"{fn['name']}\", "
                    f"source=<this C>)")
        own_score = next((score for score, flags in results if flags == base), None)
        verdict = ("The flags aren't the problem: the project's are as good as any." if own_score == best_score
                   else "Other flags come closer, but none matches: look at the C again first.")
        return head + "\n".join(lines) + "\n" + verdict

    def resolve_suggestion(self, slug: str, sid: int, apply: bool, guard: bool = False, log=lambda line: None) -> None:
        """The user's answer to a suggestion: apply it (a compiler: the project switches to it) or dismiss it."""
        hit = next((s for s in self.cfg(self._project(slug)).get("suggestions", []) if s["id"] == sid), None)
        if hit is None:
            raise NetworkError(f"no suggestion {sid}")
        from .. import function_profile_suggestions
        bundle = function_profile_suggestions.saved(self, self._project(slug), hit) if apply and hit['toolchain'] and not self.fork_slug(slug) else None
        if bundle:
            agent, _ = self.local_agent('you')
            log('Rechecking exact source/header/profile for '+bundle['function']+' only…')
            function_profile_suggestions.apply(self, agent, self._project(slug), bundle, hit['toolchain'], hit['flags'])
            log('Exact source/header/profile saved for '+bundle['function']+' only.')
        elif apply and hit["toolchain"] and hit.get("ranges") and self.fork_slug(slug):  # (its files: each if it holds)
            self.forks.set_compiler_ranges(slug, hit["ranges"], hit["toolchain"], log=log)
        elif apply and hit["toolchain"] and hit.get("ranges"):
            self.set_compiler_ranges(slug, hit["ranges"], hit["toolchain"], hit["flags"], log=log)
        elif apply and hit["toolchain"]:
            self.set_compiler(slug, hit["toolchain"], hit["flags"], guard=guard, log=log)
        self._update_cfg(slug, lambda cfg: next(s for s in cfg["suggestions"] if s["id"] == sid).__setitem__(
            "status", "applied" if apply else "dismissed"))

    @batched
    def apply_function_compiler(self, slug: str, function: str, toolchain: str, flags: list[str],
                                allow_non_improving: bool = False, preserve_other_profiles: bool = False) -> dict:
        """Revalidate a selected profile, apply one function's range, and recheck its C."""
        # Saved-file watching owns imports. Applying one DB comparison must not restore
        # the whole project through implicit _project reads or the judge's save wrapper.
        with self.progress.view() if hasattr(self, 'progress') else nullcontext():
            from ..restore_effects import batched as defer_effects
            return defer_effects(CompilerMixin._apply_function_compiler)(
                self, slug, function, toolchain, flags, allow_non_improving, preserve_other_profiles)

    def _apply_function_compiler(self, slug, function, toolchain, flags,
                                 allow_non_improving, preserve_other_profiles):
        self._rom_only(slug, "applying function compiler settings")
        proj = self._project(slug)
        fn = self._function(function, slug)
        if fn["status"] == "asm":
            raise NetworkError("This function is no longer unmatched. Run a fresh comparison.")
        source = self._comparison_source(fn)
        if not source:
            raise NetworkError("This function has no C attempt to apply. Run a fresh comparison.")
        ok, diff, _ = self._judge_with(proj, fn, toolchain, flags, source)
        if fn["status"] == "matched" and not ok:
            raise NetworkError("Matched functions can only use settings that still match byte for byte.")
        if diff is None:
            raise NetworkError("These settings could not compile or compare this function. Run a fresh comparison.")
        if not allow_non_improving and not ok and fn["best_score"] is not None and diff.score >= fn["best_score"]:
            raise NetworkError("These settings no longer improve this function. Run a fresh comparison.")
        if not ok:
            store = self.project_store(proj['id'])
            identities = {} if store is self.store else {
                'machine_ids': self._machine_agent_ids(),
                'nonexclusive_ids': self._nonexclusive_agent_ids(),
            }
            with store.tx() as db:
                self._expire_claims(db, **identities)
                if db.execute("SELECT 1 FROM claims WHERE function_id=?", (fn['id'],)).fetchone():
                    raise NetworkError('This function is being worked on. Only a verified exact match can replace active work.')
        default = self.cfg(proj)["compiler"]
        use_default = toolchain == default["toolchain"] and flags == default["flags"]
        end = fn["vram"] + fn["size"]
        if preserve_other_profiles and fn['rom_offset'] is None:
            rows = self.project_store(proj['id']).all("SELECT id,vram FROM functions WHERE project_id=? AND vram IS NOT NULL", proj['id'])
            if any(row['id'] != fn['id'] and row['vram'] == fn['vram'] for row in rows):
                raise NetworkError("This overlay shares its start address. An address-only range cannot safely isolate it.")
            # Compiler profiles are selected by function start address. Stop before
            # the next start, even when a legacy extent overlaps another overlay.
            end = min([max(fn['vram'] + 1, end)] + [row['vram'] for row in rows if row['vram'] > fn['vram']])
        own = compiler_for(self.cfg(proj), fn['vram'], fn)
        unchanged = own['toolchain'] == toolchain and list(own['flags']) == list(flags)
        if unchanged:
            pass  # Still byte-checked above; no project configuration write is needed.
        elif fn['rom_offset'] is not None:
            from ..prepared_evidence import scoped_config
            self._apply_compiler_config(proj,scoped_config(proj,fn,{'toolchain':toolchain,'flags':list(flags)}))
        else:
            self.set_compiler_ranges(slug, [[fn["vram"], end]], None if use_default else toolchain, flags)
        if fn["status"] == "matched":
            return {"said": f"Compiler settings applied to {function}; its matching C is preserved.",
                    "matched": True, "newly_matched": False, "score": 0, "match_cause": "already_matched"}
        if allow_non_improving:
            # A deliberate compiler change can lower the percentage. The old score
            # described another profile; retain the C/attempt history, rebase its score.
            with self.project_store(proj['id']).tx() as db:
                db.execute("UPDATE functions SET best_score=? WHERE id=? AND status='open'", (diff.score, fn["id"]))
        agent, _ = self.local_agent("autopilot")
        # An exact-only recheck may finish someone else's function without disturbing
        # unrelated claims. Misses never take over another worker's claim.
        self.try_quietly(agent, self._project(slug), self._function(function, slug), source,
                         synchronize=False, **({"exact_only":True} if ok else {}))
        fresh = self._function(function, slug)
        matched = fresh['status'] == 'matched'
        return {"said": (f"MATCH: {function} became byte-identical after applying compiler settings. Its verified C is saved."
                         if matched else f"Compiler settings applied to {function} only."),
                "matched": matched, "newly_matched": matched, "score": fresh['best_score'],
                "match_cause": "compiler_profile" if matched else None}

    def set_compiler_ranges(self, slug: str, ranges: list[list[int]], toolchain: str | None,
                            flags: list[str] | None = None, reopen: bool = False, log=lambda line: None) -> None:
        """Address ranges built by another compiler than the rest of the game (replacing ranges they overlap), or
        with toolchain None: those ranges back to the project's compiler. Matched functions that wouldn't match any
        more stop it, unless `reopen` (removing a range the user judged a quirk: Ridge Racer V's two GCC 2.96 ranges
        held two functions whose C only matched with 2.96): then they're open again, their C their best attempt."""
        if self.fork_slug(slug):
            raise NetworkError("a fork builds with the project's own compilers and flags")
        if toolchain:
            cfg = self.cfg(self._project(slug))
            toolchain = toolchains.get(toolchain).id
            flags = flags or consoles.default_flags(cfg, toolchain)
        flags = flags or []

        proj = self._project(slug)
        cfg = json.loads(proj["config"])
        def change(cfg):
            keep = []
            for r in cfg.get("compiler_ranges", []):
                pieces = [(r["start"], r["end"])]
                for a, b in ranges:
                    pieces = [(x, y) for start, end in pieces
                              for x, y in ((start, min(end, a)), (max(start, b), end)) if x < y]
                keep.extend(dict(r, start=a, end=b) for a, b in pieces)
            if toolchain:
                keep += [{"start": a, "end": b, "toolchain": toolchain, "flags": flags} for a, b in ranges]
            cfg["compiler_ranges"] = sorted(keep, key=lambda r: r["start"])
        change(cfg)
        self._apply_compiler_config(proj, cfg, reopen=reopen and not toolchain, log=log)
        with self.project_store(proj['id']).tx() as db:
            where = ", ".join(f"0x{a:X}-0x{b:X}" for a, b in ranges)
            self._event(db, proj["id"], None, "compiler", f"{consoles.compiler_name(toolchain)} {' '.join(flags)} for {where}"
                        if toolchain else f"the project's compiler again for {where}")

    def _reopen(self, proj, names: list[str], why: str) -> None:
        """Matched functions open again: their matching C becomes their best attempt, scored with the compiler they're
        built with now, so the next agent starts from it."""
        cfg = self.cfg(proj)
        for name in names:
            fn = self.project_store(proj['id']).one("SELECT * FROM functions WHERE project_id=? AND name=?", proj["id"], name)
            if fn is None or fn["status"] != "matched":
                continue
            own = compiler_for(cfg, fn["vram"], fn)
            _, d, _ = self._judge_with(proj, fn, own["toolchain"], list(own["flags"]), fn["matched_source"] or "")
            with self.project_store(proj['id']).tx() as db:
                db.execute("UPDATE functions SET status='open', best_source=matched_source, best_agent_id=matched_agent_id, "
                           "best_score=?, matched_source=NULL, matched_agent_id=NULL, matched_at=NULL WHERE id=?",
                           (d.score if d is not None else None, fn["id"]))
                self._event(db, proj["id"], None, "reopen", f"{name}: open again ({why})")

    def _apply_compiler_config(self, proj, cfg: dict, reopen: bool = False, log=lambda line: None) -> None:
        """Check proposed settings privately, then publish settings and scores together. A green population must
        describe today's compiler, including functions outside the old forty-function sample. Failed checks must
        not expose trial flags to working helpers, erase drafts or announce a change that never happened."""
        before = self.cfg(proj)
        keys = ("compiler", "compiler_ranges",'function_compilers')
        # Profile selection needs identity only. Narrow range edits must not load every source/target blob.
        identities = "id,name,vram,rom_offset"
        # An original-image override cannot change unrelated entry selectors. Keep
        # the broad path for ranges/defaults, including their overlapping overlays.
        old_profiles = before.get('function_compilers') or {}
        new_profiles = cfg.get('function_compilers') or {}
        changed = {k for k in old_profiles.keys() | new_profiles.keys() if old_profiles.get(k) != new_profiles.get(k)}
        scoped = (before.get('compiler') == cfg.get('compiler') and
                  before.get('compiler_ranges') == cfg.get('compiler_ranges') and len(changed) == 1)
        selector = next(iter(changed)) if scoped else None
        where, parameters = "project_id=?", (proj['id'],)
        if selector and ':' in selector:
            try:
                offset, address = map(int, selector.split(':'))
            except ValueError:
                pass
            else:
                profiles = [p for p in (old_profiles.get(selector), new_profiles.get(selector)) if p]
                if selector == f'{offset}:{address}' and all(p.get('vram') == address for p in profiles):
                    # Null-offset legacy entries can resolve an override by name.
                    names = [p.get('function') for p in profiles]
                    where += " AND ((rom_offset=? AND vram=?) OR (rom_offset IS NULL AND vram=? AND name IN (SELECT value FROM json_each(?))))"
                    parameters += (offset, address, address, json.dumps(names))
        rows = self.project_store(proj['id']).all(f"SELECT {identities} FROM functions WHERE {where} ORDER BY id", *parameters)
        ids = [f['id'] for f in rows if compiler_for(before, f["vram"], f) != compiler_for(cfg, f["vram"], f)]
        affected = self.project_store(proj['id']).all("SELECT * FROM functions WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id", json.dumps(ids))
        trial = dict(proj) | {"config": json.dumps(cfg)}
        contexts = [tuple(r) for r in self.project_store(proj['id']).all("SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version", proj["id"])]
        matched = [f for f in affected if f["status"] == "matched"]
        log(f"Checking {len(matched)} matched functions before changing compiler settings.")
        report = getattr(log, 'progress', lambda value: None)
        def matched_progress(value):
            report({**value, 'phase':'Checking affected matches', 'affected':len(affected)})
            if value['done'] == value['total'] or value['done'] % 25 == 0:
                log(f"Checked {value['done']} of {value['total']} affected matches.")
        matched_progress({'phase':'Checking affected matches', 'done':0, 'total':len(matched)})
        broken = self.regression_test(trial, None, only=matched,
                                     header_for=lambda f: self._context(proj["id"], f["unit"])["header"],
                                     progress=matched_progress) if matched else []
        if broken and not reopen:
            error = NetworkError(f"{len(broken)} matched function(s) wouldn't match with it any more "
                               f"({', '.join(n for n, _ in broken[:5])}): narrow the ranges or keep their compiler settings; when removing a range, reopen those functions explicitly")
            error.compiler_blockers = [{"function": name, "reason": reason} for name, reason in broken]
            raise error
        broken_names = {n for n, _ in broken}
        scores = {}
        rescored = [f for f in affected if f["status"] == "open" or f["name"] in broken_names]
        log(f"Updating saved scores for {len(rescored)} affected attempts.")
        def rescore(fn):
            source = fn["matched_source"] if fn["name"] in broken_names else self._source_of(fn)
            own = compiler_for(cfg, fn["vram"], fn)
            _, diff, _ = self._judge_with(trial, fn, own["toolchain"], list(own["flags"]), source) if source else (False, None, "")
            return fn['id'], diff.score if diff is not None else None, fn['name']
        report({'phase':'Updating saved scores', 'done':0, 'total':len(rescored), 'affected':len(affected)})
        # Use a purpose-owned judge group; agents keep seeing the old settings until every result is validated.
        # A single-entry apply stays in its private compile session, reusing exact
        # compiler output from validation while still judging the current target.
        from contextlib import nullcontext
        with nullcontext(None) if len(rescored) == 1 else self._pool.group('Saved attempt rescoring', proj['slug']) as pool:
            rescoring = map(rescore, rescored) if len(rescored) == 1 else pool.map(rescore, rescored)
            for done, (fid, score, name) in enumerate(rescoring, 1):
                scores[fid] = score
                report({'phase':'Updating saved scores', 'done':done, 'total':len(rescored),
                        'affected':len(affected), 'function':name})
                if done == len(rescored) or done % 25 == 0:
                    log(f"Updating score {done} of {len(rescored)}: {name}")
        # An agent can finish while checks run. Never overwrite its work with a verdict from an older snapshot.
        def population(items):
            return [tuple(r[k] for k in ("id", "name", "vram", "rom_offset", "unit", "size", "target", "relocs", "meta",
                                         "status", "matched_source", "best_source", "best_score")) for r in items]
        with self.project_store(proj['id']).tx() as db:
            current = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
            latest = db.execute(f"SELECT {identities} FROM functions WHERE {where} ORDER BY id", parameters).fetchall()
            headers = [tuple(r) for r in db.execute("SELECT unit,version FROM context_versions WHERE project_id=? ORDER BY version", (proj["id"],))]
            latest_ids = [f['id'] for f in latest if compiler_for(before, f["vram"], f) != compiler_for(cfg, f["vram"], f)]
            latest_affected = db.execute("SELECT id,name,vram,rom_offset,unit,size,target,relocs,meta,status,matched_source,best_source,best_score "
                                         "FROM functions WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id", (json.dumps(latest_ids),)).fetchall()
            if any(current.get(k) != before.get(k) for k in keys) or population(latest_affected) != population(affected) or headers != contexts:
                raise NetworkError("The project's code or settings changed during the compiler check. Try again after pausing its helpers.")
            for key in keys:
                if key in cfg:
                    current[key] = cfg[key]
                else:
                    current.pop(key, None)
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(current), proj["id"]))
            for fn in affected:
                if fn["status"] == "open":
                    db.execute("UPDATE functions SET seed=NULL, best_score=? WHERE id=?", (scores[fn["id"]], fn["id"]))
                elif fn["name"] in broken_names:
                    db.execute("UPDATE functions SET status='open', best_source=matched_source, best_agent_id=matched_agent_id, "
                               "best_score=?, matched_source=NULL, matched_agent_id=NULL, matched_at=NULL WHERE id=?",
                               (scores[fn["id"]], fn["id"]))
                    self._event(db, proj["id"], None, "reopen", f"{fn['name']}: open again (its range was removed)")
        report({'phase':'Compiler settings saved', 'done':1, 'total':1, 'affected':len(affected)})
        log("Compiler checks passed; settings and scores saved.")

    def set_compiler(self, slug: str, toolchain: str, flags: list[str] | None = None, guard: bool = True,
                     log=lambda line: None) -> None:
        """All affected matched functions must still match, for manual and automatic changes alike.
        `guard` is retained for callers from older versions; passing False no longer bypasses byte verification."""
        if self.fork_slug(slug):
            raise NetworkError("a fork builds with the project's own compilers and flags")
        proj = self._project(slug)
        tc = toolchains.get(toolchain)
        cfg = json.loads(proj["config"])
        cfg["compiler"] = {"toolchain": tc.id, "flags": flags or consoles.default_flags(cfg, tc.id), "timeout": 20}
        self._apply_compiler_config(proj, cfg, log=log)
        with self.project_store(proj['id']).tx() as db:
            self._event(db, proj["id"], None, "compiler", f"{tc.name} {' '.join(cfg['compiler']['flags'])}")
