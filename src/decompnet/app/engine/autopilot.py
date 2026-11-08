"""Autopilot: every open function's machine draft through the judge, in the background, before agents spend a
token; and what happens when a function matches (its twins are tried).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import nullcontext
from itertools import chain

from ... import compiler, m2cfix, scoring, seeds, toolchains
from ...candidate_work import trial
from ...network import Agent, NetworkError
from ...project import compiler_for, unknown_compiler_message
from ...repair_feedback import trial as repair_trial
from ..draft_context import candidate_context
from ..purpose_work import Executor


class AutopilotMixin:
    def start_autopilot(self, slug: str, workers: int | None = None, *, foreground=False) -> dict:
        """Try every open function's machine draft (m2c, compilable form) through the judge, smallest first, in the
        background unless explicitly requested with foreground=True. Matches are saved before agents spend tokens; drafts that compile
        become the function's best attempt, so agents start from them. Agents and autopilot never collide: a
        function is held only while its draft compiles, and claimed ones are skipped."""
        self.cascades.resume(slug)
        state = self.autopilots.get(slug)
        if state and state["state"] == "running":
            if foreground:
                state.setdefault('_foreground',threading.Event()).set()
                self.twins.promote(slug)
            return state
        fork = self.fork_slug(slug)
        automatic_workers = 3 if workers is None else max(1, min(32, workers))
        # Manual ROM runs can use the machine's cores. Imported builds share build
        # directories and retain their existing two-worker safety ceiling.
        width = min(32, os.cpu_count() or 4) if workers is None else automatic_workers
        workers = automatic_workers
        if fork:width = min(width, 2)
        # #118: a project from another tool may name a compiler decompdecomp doesn't have. Say which and where to pick
        # one, rather than failing later as "m2c can't read this project's code" or the registry's list of ids.
        if not fork:
            tc = self.cfg(self._project(slug))["compiler"]["toolchain"]
            if not toolchains.known(tc):
                raise NetworkError(unknown_compiler_message(tc))
        # first the twins (identical code to something matched: every project), then machine drafts where m2c reads
        # the code and the project's headers are fit for them (ROM projects; games started from scratch)
        try:
            if fork:  # a GameCube, Wii or DS game started from scratch: its own build judges
                ids = self.forks.autopilot_prepare(fork)
                try_one, workers = self.forks.autopilot_one, min(workers, 2)  # each try builds a file: fewer at once
            else:
                if not seeds.available():
                    raise NetworkError("autopilot's drafts need m2c, the draft decompiler: install it in Setup")
                proj = self._project(slug)
                tc = self.cfg(proj)["compiler"]["toolchain"]
                if not seeds.m2c_target(tc):
                    raise NetworkError("m2c can't read this project's code (no draft decompiler for it yet)")
                ids = [r["id"] for r in self.project_store(proj['id']).all("SELECT id FROM functions WHERE project_id=? AND status='open' "
                                                       "ORDER BY n_insns, vram", proj["id"])]
                try_one = self._autopilot_one
            no_drafts = ""
        except NetworkError as e:
            ids, try_one, no_drafts = [], None, str(e)
        state = {"state": "running", "total": len(ids), "tried": 0, "matched": 0, "near": 0, "twins": 0,
                 "phase": "twins", "no_drafts": no_drafts, "started_at": time.time(), "stop": threading.Event()}
        state['_foreground']=threading.Event()
        if foreground:state['_foreground'].set()
        self.autopilots[slug] = state
        threading.Thread(target=self._autopilot_run, args=(slug, ids, state, (workers, width), try_one),
                         daemon=True, name=f"autopilot-{slug}").start()
        return state

    def stop_autopilot(self, slug: str) -> None:
        self.cascades.pause(slug)
        self.twins.stop(slug)
        if (state := self.autopilots.get(slug)) and state["state"] == "running":
            state["stop"].set()

    def autopilot_status(self, slug: str) -> dict | None:
        state = self.autopilots.get(slug)
        if state:
            return {k: v for k, v in state.items() if k not in {"stop","_foreground"}}
        last = self.cfg(self._project(slug)).get("autopilot")  # the last run, from before the app restarted
        return {"state": "done", **last} if last else None

    def _autopilot_run(self, slug: str, ids: list[int], state: dict, workers: int, try_one=None) -> None:
        from .. import maintenance
        with maintenance.foreground(state.setdefault('_foreground',threading.Event())):
            self._autopilot_work(slug,ids,state,workers,try_one)

    def _autopilot_work(self, slug, ids, state, workers, try_one):
        from .. import maintenance

        automatic_workers, width = workers if isinstance(workers, tuple) else (workers, workers)
        background = threading.BoundedSemaphore(automatic_workers)
        agent, _ = self.local_agent("autopilot")
        proj = self._project(slug)
        try_one = try_one or self._autopilot_one
        try:  # identical code first: free, and it takes those functions off the drafts' list
            if slug not in self.links:  # (a room member's matches come from the host)
                job = self.twins.full_pass(slug,foreground_event=state['_foreground'])
                state["twins"] = job.matched
                state["matched"] += job.matched
        except Exception as e:
            logging.getLogger("decompdecomp").info("autopilot twins: %s", e)
        state["phase"] = "drafts"
        # One metadata read replaces a database round trip for every function.
        open_ids = {r['id'] for r in self.project_store(proj['id']).all(
            "SELECT id FROM functions WHERE project_id=? AND status='open'", proj['id'])}
        ids = [fid for fid in ids if fid in open_ids]
        state["total"] = len(ids)

        def one(fid: int) -> None:
            if state["stop"].is_set():
                return
            acquired = False
            while not state['_foreground'].is_set() and not state['stop'].is_set():
                acquired = background.acquire(timeout=.05)
                if acquired:break
            if state['stop'].is_set():
                if acquired:background.release()
                return
            manual = state['_foreground'].is_set()
            try:
                from ...compile_sessions import session
                def check_stop():
                    if state['stop'].is_set():raise NetworkError('Autopilot stopped.')
                with maintenance.foreground(state['_foreground']), session(), compiler.cancellable(check_stop), \
                        compiler._SLOTS.capacity(width) if manual else nullcontext(), \
                        compiler.throughput_slots(manual):
                    outcome = try_one(agent, proj, fid)
            except Exception:  # one odd function must not stop the rest
                outcome = "fail"
            finally:
                if acquired:background.release()
            state["tried"] += 1
            if outcome in ("match", "near"):
                state[{"match": "matched", "near": "near"}[outcome]] += 1

        remaining = iter(ids)
        queue_lock = threading.Lock()
        def lane():
            from ... import m2c_sessions
            from ...compile_sessions import session
            from ..restore_effects import streaming
            from ..trial_bookkeeping import scope as bookkeeping
            # Keep bounded preparation/output caches private to this worker across
            # functions. Session.prepare rechecks exact include bytes on every use.
            with session(), m2c_sessions.scope(state['_foreground']), streaming(self), bookkeeping():
                while not state['stop'].is_set():
                    with queue_lock:
                        fid = next(remaining, None)
                    if fid is None:return
                    one(fid)
                    from ..restore_effects import checkpoint
                    checkpoint()
        try:
            count = min(len(ids), max(automatic_workers, width))
            if count:
                with Executor(max_workers=count, purpose='Autopilot repair search', project=slug, stop=state['stop'].set) as pool:
                    list(pool.map(lambda _: lane(), range(count)))
        finally:
            state["state"] = "stopped" if state["stop"].is_set() else "done"
            state["finished_at"] = time.time()
            with self.project_store(proj['id']).tx() as db:
                cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
                cfg["autopilot"] = {k: state.get(k) for k in ("total", "tried", "matched", "near", "twins", "started_at",
                                                              "finished_at", "no_drafts")}
                db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
            drafts = state["matched"] - state.get("twins", 0)
            if drafts:  # (twins announce their own)
                with self.project_store(proj['id']).tx() as db:
                    self._event(db, proj["id"], agent.id, "autopilot",
                                f"{drafts} of {state['tried']} functions matched from machine drafts")

    def on_improvement(self, proj, function_id: int) -> None:
        from ..restore_effects import defer
        if proj is not None and not defer(self,proj,function_id,matched=False):
            self.cascades.notify(proj, function_id)

    def on_match(self, proj, function_id: int, agent: Agent | None = None) -> None:
        """Its twins get its code, at once. (Not for the full twins pass's own matches while it runs: it tries
        those twins itself.)"""
        if proj is None:
            return
        from ..restore_effects import defer
        if defer(self,proj,function_id,matched=True):return
        from .. import repair_mappings
        repair_mappings.harvest(self.twins, proj, function_id)
        self.twins.index_function(proj, function_id)
        if library := getattr(self, "match_library", None):
            library.capture(proj, function_id)
        self.cascades.notify(proj, function_id)
        if agent is not None and agent.name == "autopilot" and self.twins._full_running(proj["slug"]):
            return
        self.twins.after_match(proj, function_id)
        if any(c["status"] in ("checking", "waiting") for c in self.cfg(proj).get("range_checks", [])):
            self.rangechecks.recheck(proj["slug"])  # a neighbour of a suggested range may have spoken

    @trial
    def try_quietly(self, agent, proj, fn, source: str, before_record=None, *, exact_only=False, synchronize=True) -> dict:
        """Judge a scoped candidate. Exact-only rechecks may supersede a claim after an exact save;
        ordinary autopilot attempts acquire their own short claim. {"matched", "score"}."""
        with self.progress.judging(proj["slug"], **({"synchronize":False} if not synchronize else {})) if hasattr(self, "progress") else nullcontext():
            return AutopilotMixin._try_quietly(self, agent, proj, fn, source, **({'before_record': before_record} if before_record is not None else {}), exact_only=exact_only)

    def _try_quietly(self, agent, proj, fn, source: str, before_record=None, *, exact_only=False) -> dict:
        from ..boundary_repairs import blocked
        if fn["id"] in blocked(self,proj):raise NetworkError("Review this function boundary before starting a local search.")
        fn = self.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", fn["id"])
        if fn is None or fn["status"] != "open":
            raise NetworkError("This function is no longer open. Read the updated project before trying another draft.")
        if not exact_only:
            now = time.time()
            store = self.project_store(proj['id'])
            identities = {} if store is self.store else {
                'machine_ids': self._machine_agent_ids(), 'nonexclusive_ids': self._nonexclusive_agent_ids()}
            with store.tx() as db:
                self._expire_claims(db, **identities)
                if db.execute("SELECT 1 FROM claims WHERE function_id=?", (fn["id"],)).fetchone():
                    raise NetworkError(f"{fn['name']} is claimed")
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + 300))
        try:
            ctx = self._context(proj["id"], fn["unit"])
            if exact_only:
                from ..exact_match_inputs import capture
                exact_inputs = capture(self, proj, fn, ctx, source)
            result = self._compile(proj, ctx["header"], source, fn)
            if not result.ok:
                return {"matched": False, "score": None}
            cand, verdict, _ = self._evaluate(fn, proj, result.obj)
            if cand is None:
                return {"matched": False, "score": None}
            d = scoring.diff(self._target_obj(fn, proj), cand)
            if verdict is not None:
                d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
            if exact_only and not d.bytes_match:
                return {"matched":False,"score":d.score}
            self._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match,
                         effective_settings=getattr(result, 'effective_settings', None),
                         before_record=before_record, **({"exact_inputs":exact_inputs} if exact_only else {}))
            from ...source_lines import mismatches
            from ..repair_localisation import bind
            locations=bind(source,mismatches(result.obj,fn['name'],d.rows,source,
                filename='func.cpp' if getattr(result,'language','c')=='c++' else 'func.c',arch=proj['arch']))
            from ..compiler_diagnostics import output_fingerprint
            return {"repair_locations":locations,"matched": d.bytes_match, "score": d.score, "max_score": d.max_score,
                    "output_fingerprint":output_fingerprint(result.obj),
                    "percent": d.percent, "rows": self._search_rows(fn,proj,result.obj,d.rows),
                    "candidate_target": cand.code.hex(),
                    "candidate_relocs": json.dumps([(r.offset, r.type, 0) for r in cand.relocs])}
        finally:
            if not exact_only:
                with self.project_store(proj['id']).tx() as db:
                    db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (fn["id"], agent.id))

    def _autopilot_one(self, agent, proj, fid: int) -> str:
        """match | near | far | fail | skip, for one function's draft. A draft autopilot already judged with the same
        header is skipped: autopilot tries a function again when its draft changed (the background drafter asks it
        to after the header or the compiler changed, app/drafts.py), not to judge the same C twice."""
        with self.progress.judging(proj["slug"], synchronize=False) if hasattr(self, "progress") else nullcontext():
            return AutopilotMixin._autopilot_one_work(self, agent, proj, fid)

    def _autopilot_one_work(self, agent, proj, fid: int) -> str:
        now = time.time()
        store = self.project_store(proj['id'])
        identities = {} if store is self.store else {
            'machine_ids': self._machine_agent_ids(), 'nonexclusive_ids': self._nonexclusive_agent_ids()}
        with store.tx() as db:
            self._expire_claims(db, **identities)
            fn = db.execute("SELECT * FROM functions WHERE id=?", (fid,)).fetchone()
            if fn is None or fn["status"] != "open" or db.execute("SELECT 1 FROM claims WHERE function_id=?", (fid,)).fetchone():
                return "skip"
            db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fid, agent.id, now, now + 300))
        try:
            from ..return_repair_autopilot import run as return_repair
            if return_repair(self, agent, proj, fn):
                return 'match'
            ctx = self._context(proj["id"], fn["unit"])
            plan, toolchain, context = self._draft_plan(fn, proj, ctx=ctx)
            own = compiler_for(self.cfg(proj), fn["vram"], fn)
            data_asm = "\n".join(self._jtbl_asm(proj, fn))
            from .. import draft_reports, initial_draft_repairs
            input_key = draft_reports.key(plan, ctx['header'], data_asm, fn)
            previous = draft_reports.read(self, fid, project_id=proj['id'])
            external_inputs = own.get('cmd') or own.get('ro_paths') or any(flag.startswith(('-I','-include','-imacros')) for flag in own.get('flags',[]))
            if not external_inputs and previous.get('key') == input_key and previous.get('complete'):
                return 'skip'
            target_asm = self._asm_text(fn, proj)
            # Eligibility is known before any generation. Reuse this exact captured
            # plan and assembly rather than calculating them again inside _seed_for.
            seed = self._seed_for(fn, proj, (plan, toolchain, context, data_asm, target_asm))
            if not seed:
                # Seed preparation may have just recorded a generation failure.
                # Reuse that receipt instead of immediately repeating failed m2c.
                previous = draft_reports.read(self, fid, project_id=proj['id'])
            data_key = hashlib.sha256((data_asm or '').encode()).hexdigest()
            report = {'key': input_key, 'seed_key':plan, 'data_key':data_key, 'compiled': False, 'scored': False, 'matched': False, 'trials': 0, 'failures': []}
            from .briefs import DRAFT_NOTE
            prefix = DRAFT_NOTE+'\n'+seeds.M2C_MACROS+'\n'
            seed_row = self.project_store(proj['id']).one('SELECT seed_key FROM functions WHERE id=?',fid)
            if (seed and seed.startswith(prefix) and seed_row and seed_row['seed_key'] == plan
                    and previous.get('data_key',data_key) == data_key):
                draft = seed[len(prefix):]
            elif previous.get('seed_key') == plan and previous.get('failures'):
                report['failures'] = previous['failures']
                draft = None
            else:
                try:
                    draft = seeds.generate_from_asm(target_asm, fn['name'], toolchain, context, data_asm, valid=True, strict=True, timeout=20)
                except seeds.DraftError as error:
                    draft_reports.failure(report, 'generation', 'raw_m2c', error)
                    draft = None
            if not draft:
                report['complete'] = True
                draft_reports.save(self, fid, report, project_id=proj['id'])
                return 'fail'
            # (its compiler named in it: the same draft is worth judging again with another compiler)
            note = f"/* machine draft (m2c) for {toolchain} {' '.join(own.get('flags', []))}, tried by autopilot */\n"
            candidates = [('raw_m2c', f"{note}{seeds.M2C_MACROS}\n{draft}\n")]
            from ..loop_storage_shapes import variants as loop_shapes
            for family,source in loop_shapes(draft,fn['name'],ctx['header'],proj['arch'])[:2]:
                candidates.append((family,f"{note}{seeds.M2C_MACROS}\n{source}\n"))
            if (seeds.m2c_target(toolchain) or "").startswith("mips"):
                # next to m2c's own draft, the same with its usual mistakes undone (m2cfix.py): few lines change
                refined = m2cfix.refine_draft(draft, fn["name"], target_asm)
                if refined:
                    candidates.append(('refined_m2c', f"{note}{seeds.M2C_MACROS}\n{refined}\n"))
            from ..capture_followup_repairs import callee_evidence
            from ..recent_capture_repairs import variants as captured_variants
            known_callees=callee_evidence(self,proj,fn,draft)
            for family,source in captured_variants(draft,fn['name'],'mips' if (seeds.m2c_target(toolchain) or '').startswith('mips') else '',target_asm,ctx['header'],known_callees):
                if family in ('overlay_callee_alias','argument_home_slot','untouched_argument_forwarding'):
                    candidates.append((family,f"{note}{seeds.M2C_MACROS}\n{source}\n"))
            for canonical in (False,True):
                if array := m2cfix.stack_array(draft,fn['name'],canonical):
                    family = 'stack_array_indexed' if canonical else 'stack_array'
                    candidates.append((family, f"{note}{seeds.M2C_MACROS}\n{array}\n"))
            def local_candidates():
                # Lazy fallback: successful normal drafts never pay for another m2c invocation.
                # Imported projects use their file-context drafting route; forwarding their saved
                # declarations here remains planned (ROADMAP).
                if not fn['best_source']:
                    return
                roots = self._current_names(proj, seeds.asm_names(fn['asm'] or ''))
                alternative = candidate_context(ctx['header'], fn['best_source'], roots, fn['name'])
                if not alternative:
                    return
                try:
                    local = seeds.generate_from_asm(self._asm_text(fn, proj), fn['name'], toolchain,
                                                   alternative, data_asm, valid=True, strict=True)
                except seeds.DraftError:
                    return
                if not local:
                    return
                yield 'local_context_m2c', f"{note}{seeds.M2C_MACROS}\n{local}\n"
                if (seeds.m2c_target(toolchain) or '').startswith('mips'):
                    refined = m2cfix.refine_draft(local, fn['name'], fn['asm'] or '')
                    if refined and refined != local:
                        yield 'local_context_refined', f"{note}{seeds.M2C_MACROS}\n{refined}\n"

            def recovery_candidates():
                if report['compiled']:
                    return
                for index, (family, source) in enumerate(initial_draft_repairs.recovery_drafts(
                        draft, fn['name'], target_asm, proj['arch'], toolchain, context, data_asm, report)):
                    if report['compiled'] or index >= 6:
                        return
                    yield family, f"{note}{seeds.M2C_MACROS}\n{source}\n"

            from ..compiler_shape_repairs import machine_draft_copies
            from ..narrow_store_repairs import machine_drafts as narrow_drafts
            outcomes, seen = [], set()
            for family, source in chain(narrow_drafts(machine_draft_copies(chain(candidates, local_candidates()), fn['name'], ctx['header']),fn['name'],ctx['header'],proj['arch']), recovery_candidates()):
                if source in seen:
                    continue
                seen.add(source)
                result = self._compile(proj, ctx["header"], source, fn)
                report['trials'] += 1
                if not result.ok:
                    draft_reports.failure(report, 'compiler', family, result.log)
                else:
                    report['compiled'] = True
                    report['family'] = family
                    report['compiled_source'] = source
                cand, verdict, _ = self._evaluate(fn, proj, result.obj) if result.ok else (None, None, None)
                if cand is None:
                    if result.ok:
                        draft_reports.failure(report, 'link', family, 'Compiled C could not be evaluated against this target; review its symbols and data mapping.')
                    outcomes.append("fail")
                    continue
                d = scoring.diff(self._target_obj(fn, proj), cand)
                if verdict is not None:
                    d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
                # Keep the generating path visible: an already automatic match need not be mistaken
                # for an unimplemented agent repair when this record is reviewed later.
                with repair_trial({'kind': 'machine draft', 'family': family}):
                    self._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match,
                                 effective_settings=getattr(result, 'effective_settings', None))
                report['scored'] = True
                report['matched'] = d.bytes_match
                report.pop('compiled_source', None)  # scored source is already retained in attempts
                outcomes.append("match" if d.bytes_match else "near" if d.percent >= 90 else "far")
                if d.bytes_match:
                    break
            report['complete'] = True
            draft_reports.save(self, fid, report, project_id=proj['id'])
            fresh = self._function(fn['name'], proj['slug'])
            if fresh['status'] == 'open' and fresh['best_source'] != fn['best_source'] and return_repair(self, agent, proj, fresh):
                return 'match'
            return min(outcomes or ['fail'], key=("match", "near", "far", "skip", "fail").index)
        finally:
            with self.project_store(proj['id']).tx() as db:
                db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (fid, agent.id))
