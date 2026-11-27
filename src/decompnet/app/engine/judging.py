"""The judge and the work around it: each function's target (its bytes as the ROM has them), claims, attempts
(compile, link at the function's address, compare), batches, giving a function up, and what agents spend.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from ... import compile_sessions, compile_timing, consoles, scoring
from ...candidate_work import trial
from ...network import MACHINE_AGENTS, Agent, Network, NetworkError, large_plateau, stall_line
from ...release_receipts import operation as release_operation
from ...request_timing import build as timed_build
from ...request_timing import timed_lock
from ...rom import match
from ...rom.symbols import SymbolTable
from ...store import in_transaction
from .. import attempt_workers
from ..function_names import canonical_source
from .common import _AUTO_TOKEN, BATCH_INSNS

_search_evaluation = ContextVar("search_evaluation", default=False)


@contextmanager
def search_evaluation():
    token = _search_evaluation.set(True)
    try:yield
    finally:_search_evaluation.reset(token)

# Releases with no compile pushed back per claim with the full advice; then the agent is told to stop (_push_back)
UNCOMPILED_PUSH_BACKS = 2


class JudgeMixin:
    def _compile(self, proj, header: str, source: str, fn=None):
        if fn is not None and self.is_rom(proj):
            source = canonical_source(fn, source)
        return super()._compile(proj, header, source, fn)

    def _compile_batch(self, proj, header: str, sources, fn):
        if self.is_rom(proj):
            sources = [canonical_source(fn, source) for source in sources]
        return super()._compile_batch(proj, header, sources, fn)

    def _rom_target(self, fn) -> match.RomTarget:
        project_id = fn['project_id']
        store = self.project_store(project_id)
        session = compile_sessions.current()

        def load_config():
            return self.cfg(store.one("SELECT config FROM projects WHERE id=?", project_id))

        if session is None or in_transaction():
            cfg = load_config()
            return consoles.of(cfg).target_from_row(fn, cfg)

        # A session's target hit needs only the committed project revision. Bypass
        # target caching in write transactions because rollback can reuse a revision.
        tracked = getattr(store, 'project_rows_tracked', False)
        if tracked:
            revision = store.read_version('project_rows', project_id)
        else:
            cfg = load_config()
            revision = repr(cfg)
        row = dict(fn)
        key = ('rom', id(self), project_id, revision,
               tuple(row.get(k) for k in ('name', 'target', 'relocs', 'vram', 'rom_offset', 'meta')))
        if key in session.targets:
            return session.targets[key]
        if tracked:
            cfg = store.read_cache.get(('rom-target-config', project_id), revision, load_config)
        target = consoles.of(cfg).target_from_row(fn, cfg)
        session.targets[key] = target
        while len(session.targets) > 16:
            session.targets.popitem(last=False)
        return target

    def _make_target(self, proj, rf, syms: SymbolTable):
        cfg = self.cfg(proj)
        return self.console(proj).target_from_split(rf, syms.resolve, cfg.get("big_endian", True), cfg.get("gp", 0))

    def _target_obj(self, fn, proj):
        session = compile_sessions.current()
        if session is not None and self.is_rom(proj):
            syms = self.symbols(proj['id'])
            row = dict(fn)
            key = (id(self), proj['id'], proj['config'], id(syms),
                   tuple((k, row.get(k)) for k in ('id', 'name', 'target', 'relocs', 'vram', 'meta', 'unit', 'size', 'rom_offset')),
                   self._generation.get(proj['id'], 0))
            if key in session.targets:
                return session.targets[key][1]
        else:key = None
        if not self.is_rom(proj):
            obj = super()._target_obj(fn, proj)
        else:
            obj = self.console(proj).target_object(fn, self.cfg(proj), self.symbols(proj['id']))
        obj.cpu = self.console(proj).cpu_variant
        if key is not None:
            # Keep the symbol table alive so object-id reuse cannot produce a stale hit.
            session.targets[key] = (syms, obj)
            while len(session.targets) > 16:session.targets.popitem(last=False)
        return obj

    def _evaluate(self, fn, proj, obj: bytes, *, diagnostics=True):
        from ...attempt_status import update
        update('checking', fn['name'])
        if not self.is_rom(proj):
            return super()._evaluate(fn, proj, obj)
        # The verdict needs only the link. The advisory diagnostics and their search cache belong to the full engine;
        # an engine without them (a bare Core, as tests/test_overlay_reader.py judges with) gets the verdict alone.
        diagnose = getattr(self, '_link_diagnostics', None) if diagnostics else None
        link_scope = self._diagnostic_scope(fn, proj) if diagnose is not None else None
        with compile_timing.phase('link'):
            target = self._rom_target(fn)
            # the console links it (GBA: ARM/Thumb, PS3: PPC64; the rest: rom.match), against the caller's overlay (#230)
            symbols = self.symbols(proj["id"])

            console = self.console(proj)

            def local_link():
                from ...rom.verifier_link import PackReader, project_inputs

                if project_inputs.get() and type(target) is match.RomTarget and "rom_offset" in fn.keys():
                    read = PackReader(self, proj, fn)
                else:
                    read = self.reader(proj, fn)
                session = compile_sessions.current()
                if not (_search_evaluation.get() and link_scope is not None and session is not None):
                    return console.link_function(obj, fn["name"], target, symbols, read)

                from ...rom.link_cache import SearchLinks

                if session.links is None:
                    session.links = SearchLinks()
                scope = (self, proj['id'], proj.get('root') if isinstance(proj, dict) else proj['root'], console,
                         link_scope)
                return session.links.link(scope, target, symbols, obj, read,
                                          lambda reader: console.link_function(obj, fn["name"], target, symbols, reader))

            res = (attempt_workers.run("rom-link", (dict(proj), dict(fn), obj, target, symbols), local_link)
                   if attempt_workers.active() and "rom_offset" in fn.keys() else local_link())
        if diagnose is None:
            return res.candidate, res.exact if res.candidate is not None else None, res.problems
        if _search_evaluation.get():
            # The exact verdict always comes from the full linker. Only advisory diagnostics wait.
            local = self.__dict__.setdefault('_search_link_local', threading.local())
            local.last = (fn['id'], hashlib.sha256(obj).digest(), res, target)
            local.guard=link_scope
            return res.candidate, res.exact if res.candidate is not None else None, res.problems
        local = self.__dict__.get('_search_link_local')
        if local is not None and hasattr(local, 'last'):del local.last
        return diagnose(fn,proj,obj,res,target)

    def _diagnostic_scope(self,fn,proj):
        if not hasattr(self,'project_store'):return None
        store=self.project_store(proj['id'])
        if not hasattr(store,'read_version'):return None
        return (store.read_version('inputs',proj['id']),getattr(self,'_generation',{}).get(proj['id'],0),
                proj.get('config') if isinstance(proj,dict) else proj['config'],
                tuple((key,dict(fn).get(key)) for key in ('name','target','relocs','vram','meta','size','unit','rom_offset')))

    def _complete_search_evaluation(self,fn,proj,obj):
        local=self.__dict__.get('_search_link_local')
        last=getattr(local,'last',None)
        if (last is not None and last[:2]==(fn['id'],hashlib.sha256(obj).digest())
                and getattr(local,'guard',None) is not None
                and local.guard==self._diagnostic_scope(fn,proj)):
            del local.last
            return self._link_diagnostics(fn,proj,obj,*last[2:])
        return self._evaluate(fn,proj,obj)

    def _link_diagnostics(self,fn,proj,obj,res,target):
        from ...object_emission import inspect as emission_inventory
        from ..candidate_diagnostics import export_layout, text, unresolved
        checks = {'linked_bytes': {'exact': res.exact}, 'link': unresolved(res.problems),
                  'object_emission': emission_inventory(obj, fn['name']),
                  'export': export_layout(obj, fn['name'], res.exact, res.placements)}
        if res.exact and not checks['export'].get('ready'):
            from ..compiler_context import verified
            context=verified(self,proj,fn,obj)
            if context:checks['export']=context
        checks['instructions'] = {'exact': res.instructions_exact}
        checks['data'] = [p for p in res.problems if p.startswith('your data for ')]
        if res.instructions_exact and getattr(res, 'data_mismatches', ()):
            from .. import switch_tables
            checks['jump_tables'] = switch_tables.differences(self, proj, fn, res)
        address_rows = []
        if res.linked_code is not None and res.candidate is not None and not res.problems and proj['arch'] == 'mips':
            from decomprepair.mips_addresses import aligned
            rows = self._compare_candidate(proj, self._target_obj(fn, proj), res.candidate, advisory=True).rows
            address_rows = aligned(rows, target.code, res.linked_code, bool(proj['big_endian']))
            checks['address_mismatches'] = [row for row in address_rows
                                           if row['target'] is not None and row['candidate'] is not None
                                           and row['target'] != row['candidate']]
        # Actual linked operands, rather than synthetic relocation-label rendering, must steer
        # a register-only search. This is advisory and never changes the link verdict or score.
        allocation_rows = None
        if res.linked_code is not None and res.candidate is not None and not res.problems:
            from ...elfobj import FunctionObj
            from ..allocation_evidence import diagnose
            from ..allocation_evidence import text as allocation_text
            cpu = self._target_obj(fn,proj).cpu
            physical = self._compare_candidate(
                proj, FunctionObj(fn['name'], proj['arch'], bool(proj['big_endian']), target.code, cpu=cpu),
                FunctionObj(fn['name'], proj['arch'], bool(proj['big_endian']), res.linked_code, cpu=cpu),
                advisory=True).rows
            diagnosis = diagnose(physical,proj['arch'])
            if diagnosis['register_only'] and diagnosis['register_cycles']:
                allocation_rows = physical
                checks['allocation'] = diagnosis
                checks['allocation_summary'] = allocation_text(physical,proj['arch'])
        checks['message'] = text(checks)
        if allocation_rows:checks['message'] += '\nPhysical linked-byte '+checks['allocation_summary']
        cache = self.__dict__.setdefault('_candidate_diagnostics', {})
        with self.__dict__.setdefault('_candidate_diagnostics_lock', threading.Lock()):
            cache[fn['id'], hashlib.sha256(obj).hexdigest()] = (checks, address_rows, allocation_rows)
            while len(cache) > 256:
                del cache[next(iter(cache))]
        return res.candidate, res.exact if res.candidate is not None else None, res.problems

    def _candidate_checks(self, fn, proj, obj: bytes) -> dict:
        return self.__dict__.get('_candidate_diagnostics', {}).get((fn['id'], hashlib.sha256(obj).hexdigest()), ({}, []))[0]

    def _candidate_addresses(self, fn, obj: bytes):
        return self.__dict__.get('_candidate_diagnostics', {}).get((fn['id'], hashlib.sha256(obj).hexdigest()), ({}, []))[1]

    def _search_rows(self, fn, proj, obj, fallback):
        if not isinstance(obj,(bytes,bytearray,memoryview)):
            return fallback
        local = self.__dict__.get('_search_link_local')
        last = getattr(local, 'last', None)
        if last is not None:
            del local.last
        cached = self.__dict__.get('_candidate_diagnostics', {}).get((fn['id'],hashlib.sha256(obj).hexdigest()))
        if last is not None and last[:2] == (fn['id'], hashlib.sha256(obj).digest()):
            res, target = last[2:]
            if res.linked_code is not None and res.candidate is not None and not res.problems:
                from ...elfobj import FunctionObj
                from ..allocation_evidence import diagnose
                # Search actually consumes physical register cycles. Keep those rows without
                # producing unrelated export/address diagnostics or relinking the object.
                cpu = self._target_obj(fn, proj).cpu
                with compile_timing.phase('search_diagnostics'):
                    physical = scoring.diff(
                        FunctionObj(fn['name'],proj['arch'],bool(proj['big_endian']),target.code,cpu=cpu),
                        FunctionObj(fn['name'],proj['arch'],bool(proj['big_endian']),res.linked_code,cpu=cpu)).rows
                    diagnosis = diagnose(physical, proj['arch'])
                if diagnosis['register_only'] and diagnosis['register_cycles']:
                    return physical
            return fallback
        return cached[2] if cached and len(cached)>2 and cached[2] is not None else fallback

    def rename_tokens(self, proj, text: str) -> str:
        """Show automatic names under their current names (func_80001234 -> deflate)."""
        syms = self.symbols(proj["id"])
        def renamed(match):
            original = match.group(0)
            address = int(original[-8:], 16)
            name, offset = syms.name_at(address)
            if original.startswith('func_'):
                # A containing symbol is useful for data, but is not a callable C name.
                # Overlay addresses can also alias data in another image.
                symbol = syms.by_name.get(name)
                return name if not offset and symbol and symbol.kind == 'func' else original
            return syms.label(address)
        return _AUTO_TOKEN.sub(renamed, text)

    def claim(self, agent: Agent, project: str | None = None, function: str | None = None, strategy: str = "easiest") -> str:
        function = self.focus_target(agent,project,function)
        if (fork := self.fork_slug(project)):
            return self.forks.claim(agent, fork, function, strategy)
        link = self._link(project)
        if link is None:
            return super().claim(agent, project, function, strategy)
        if function:
            function = self._function(function, project)["name"]
        name, why = link.claim(agent, function, strategy)
        if name is None:  # (the host's word: what's left and why, as its own agents hear it)
            return why or "Nothing left to claim in this room — every open function is matched or someone is on it. 🎉"
        link.sync()
        fn = self._room_claim_row(agent, project, name, strategy)
        return self._brief(fn, agent, header="CLAIMED")

    def claim_many(self, agent: Agent, project: str | None, count: int, strategy: str = "easiest") -> str:
        """Several small functions at once (count 2-6): the next one by `strategy` and, when it's small, its
        neighbours in the same file. Most small functions match on the first try, so an agent writes them all and
        judges them in one compile_and_diff: a fraction of the turns. Big functions come alone."""
        if agent.id in getattr(self,'_agent_focus',{}):return self.claim(agent,project,None,strategy)
        count = max(1, min(int(count or 1), 6))
        if count == 1 or self._link(project) is not None:
            return self.claim(agent, project, None, strategy)
        proj = self._project(project)
        held = self.project_store(proj['id']).all("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? "
                              "AND f.project_id=? ORDER BY f.vram, f.id", agent.id, proj["id"])
        if not held:
            text = self.claim(agent, project, None, strategy)  # the first, the usual way (with its "nothing left")
            held = self.project_store(proj['id']).all("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? "
                                  "AND f.project_id=?", agent.id, proj["id"])
            if not held:
                return text
            if held[0]["n_insns"] > BATCH_INSNS:
                return f"Requested {count} functions; returned 1 because it exceeds the {BATCH_INSNS}-instruction batch limit. Work on the held function before claiming again.\n\n" + text
        first = held[0]
        from ...routing_experiment import exclusive
        if exclusive(self, proj, first['id'], agent.id):
            return 'Routing comparison is measuring one function for this claim.\n\n' + self.claim(agent, project, first['name'], strategy)
        rows = list(held)
        if first["n_insns"] <= BATCH_INSNS and len(rows) < count:
            rows += self._batch_more(agent, proj, first, count - len(rows), strategy)
        if len(rows) == 1:
            reason = (f"it exceeds the {BATCH_INSNS}-instruction batch limit" if first["n_insns"] > BATCH_INSNS
                      else "no other eligible small functions in this file passed the queue filters")
            return f"Requested {count} functions; returned 1 because {reason}. Work on the held function before claiming again.\n\n" + self.claim(agent, project, None, strategy)
        rows = list({r["id"]: r for r in rows}.values())
        names = [r["name"] for r in rows]
        fork = self.fork_slug(project)
        head = (f"## CLAIMED {len(rows)} small functions: {', '.join(names)}\n"
                f"Write all of them, then judge them in one call: compile_and_diff(function=\"{', '.join(names)}\", "
                f"source=<all {len(rows)} definitions>). Each is judged on its own and saved as soon as it matches; "
                "then fix what's left (one at a time is fine). release_function any you can't do.")
        briefs = [self.forks.brief(r, agent, "CLAIMED") if fork else self._brief(r, agent, "CLAIMED") for r in rows]
        return "\n\n---\n\n".join([head, *briefs])

    def _batch_more(self, agent: Agent, proj, first, n: int, strategy: str = "easiest") -> list:
        """Up to n more small open functions in the same file, using the normal queue and filters, claimed for the agent."""
        sdk=self.sdk_functions(proj)
        deferred=list(sdk) if first['id'] not in sdk else []
        deferred += [r["function_id"] for r in self.project_store(proj['id']).all("SELECT function_id FROM claims WHERE agent_id=?", agent.id)]
        rows = []
        for _ in range(n):
            row = Network.claim_row(self, agent, proj['slug'], strategy=strategy,
                                    batch_from={'unit':first['unit'] or '', 'limit':BATCH_INSNS, 'deferred':deferred})
            if row is None:
                break
            if row["id"] in deferred:
                break
            deferred.append(row["id"])
            rows.append(row)
        return rows

    def attempt_many(self, agent: Agent, names: list[str], source: str, project: str | None = None) -> str:
        """compile_and_diff for several functions in one source: each judged on its own (a fork's, with the other
        definitions taken out, as its file gets them one by one), in order."""
        fork = self.fork_slug(project)
        out, matched = [], 0
        for name in names:
            try:
                one = self.forks.one_definition(fork, source, name, [n for n in names if n != name]) if fork else source
                res = self.attempt(agent, name, one, project)
                res["text"] += self.attempt_limit_note(agent, name, project)
            except NetworkError as e:
                res = {"text": f"❌ {e}", "matched": False}
            matched += bool(res.get("matched"))
            out.append(f"### {name}\n{res['text']}")
        return f"{matched} of {len(names)} matched.\n\n" + "\n\n".join(out)

    def _yield_autopilot(self, function: str | None, project: str | None, *, check_cancel=None) -> bool:
        """Wait for an autopilot draft and return whether a wait was needed."""
        # Saved verification already waited outside its verdict lock. Repeating
        # that wait here can strand the autopilot waiting to publish under it.
        if getattr(self._compile_ahead_local, 'strict', False):return False
        if not function:
            return False
        try:
            fn = self._function(function, project)
            fid = fn["id"]
        except NetworkError:
            return False
        for poll in range(100):
            if check_cancel is not None:check_cancel()
            held = self.project_store(fn['project_id']).one('SELECT agent_id FROM claims WHERE function_id=?', fid)
            identity = self.store.one('SELECT name FROM agents WHERE id=?', held['agent_id']) if held else None
            if not identity or identity['name'] != 'autopilot':
                return poll > 0
            time.sleep(0.2)
        return True

    def attempt(self, agent: Agent, function: str, source: str, project: str | None = None, *, publish: bool = True) -> dict:
        # The editor leaves saved-folder publication to autosave. Its judge still checks the inputs explicitly.
        with self.progress.foreground(), (nullcontext() if publish else self.progress.view()):
            fn = self._function(function, project)
            slug = self.project_store(fn['project_id']).one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"]
            with self.progress.judging(slug, publish=publish), attempt_workers.attempt(self):
                from ...attempt_status import update
                update('compiling', fn['name'])
                try:
                    return self._attempt(agent, function, source, project)
                finally:
                    self.progress.foreground_changed(slug, fn['name'])

    def _record(self, fn, *args, **kwargs):
        from ...attempt_status import update
        from .. import match_commits
        update('saving', fn['name'])
        store = self.project_store(fn['project_id'])
        slug = store.one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"]
        matching = kwargs.get('matched', args[4] if len(args) > 4 else False)
        proj = store.one('SELECT * FROM projects WHERE id=?', fn['project_id']) if matching and not self.progress.applying else None
        automatic = proj is not None and self.cfg(proj).get('auto_commit_matches', False)
        verdict = None
        with timed_lock(self.progress._project_lock(slug), 'judge_publish_lock_wait'):
            # attempt()'s judging block already read progress just before this record; a second full read here made
            # every compile check the folder twice before and once after.
            if not self.progress.applying and not getattr(self.progress.local, "judging", None):
                self.progress.sync(slug, force=True)
            self.progress.check_result()
            before = store.one('SELECT status FROM functions WHERE id=?', fn['id']) if automatic else None
            result = super()._record(fn, *args, **kwargs)
            if before is not None and before['status'] != 'matched':
                verdict = match_commits.capture_verdict(store, fn['id'])
        if proj is not None and kwargs.get('replacement') and verdict is None:
            # a new exact source for a matched function commits like a polished one: the same function, new C
            match_commits.enqueue(self, proj, fn['id'], polished=True)
        if verdict is not None:
            # Queueing waits for the database writer. Other judges can use the project lock meanwhile.
            match_commits.enqueue(self, proj, fn['id'], verdict=verdict)
        return result

    def _remember_search_output(self, agent, function, source, project, result):
        """Give manual/agent trials the same compact output history as local search."""
        if result.get('score') is None or result.get('repeat'):
            return
        fn = self._function(function, project)
        proj = self.project_row(project) if project else self.project_store(fn['project_id']).one(
            'SELECT * FROM projects WHERE id=?', fn['project_id'])
        measured = result.get('compiled_output') or {}
        output = measured.get('fingerprint') if measured.get('granularity') == 'function' else None
        from ..search_evidence import remember
        remember(self, agent, proj, fn, source, result['score'], output=output)

    def _attempt(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        if self._yield_autopilot(function, project) and self._function(function, project)['status'] == 'matched':
            raise NetworkError(f"{function} is already matched. Call claim_function for the next function.")
        if (fork := self.fork_slug(project)):
            res = self.forks.attempt(agent, fork, function, source)
            self._remember_search_output(agent, function, source, project, res)
            return res
        link = self._link(project)
        before = self._function(function, project)["best_score"] if link else None
        res = super().attempt(agent, function, source, project)
        self._remember_search_output(agent, function, source, project, res)
        if link is None or res["score"] is None:
            return res
        name = self._function(function, project)["name"]
        if res["matched"]:
            verdict = link.attempt(agent, name, source)
            if not verdict.get("matched"):
                res["text"] += ("\n⚠️ The room's host could not confirm this match on their copy (score "
                                f"{verdict.get('score')}). {verdict.get('reason') or 'Check compiler settings.'} "
                                "The source and compiler/context evidence are retained for host retry.")
            else:
                res["text"] += "\nShared with the room: the host confirmed the match."
        elif before is None or res["score"] < before:
            threading.Thread(target=lambda: self._report(link, agent, name, source, res["score"]), daemon=True).start()
        return res

    @staticmethod
    def _report(link, agent, name, source, score) -> None:
        try:
            link.call("/collab/best", agent, function=name, source=source, score=score)
        except NetworkError:
            pass

    def apply_reported_best(self, slug: str, name: str, score: int, source: str, agent: Agent, *, before_record=None) -> None:
        """Host side: a room member's better attempt, as they report it (their own compile scored it)."""
        fn = self._function(name, slug)
        if fn["status"] != "open" or score <= 0 or (fn["best_score"] is not None and score >= fn["best_score"]):
            return
        self._record(fn, agent, score, source, self._context(fn["project_id"], fn["unit"])["version"], via="room", before_record=before_record)

    @release_operation("release")
    def release(self, agent: Agent, function: str, notes: str, project: str | None = None, not_c: bool = False, claim_token: str = "", *, handoff=None, room_evidence=None) -> str:
        if (fork := self.fork_slug(project)):
            return self.forks.release(agent, fork, function, notes, not_c, claim_token=claim_token, handoff=handoff,
                                      room_evidence=room_evidence)
        link = self._link(project)
        if link is not None:
            from ...agent_notes import room_handoff

            fn = self._function(function, project)
            evidence = room_handoff(self.project_store(fn['project_id']), fn, agent, notes)
            link.call("/collab/release", agent, function=fn["name"], notes=notes, not_c=not_c,
                      note_evidence=evidence)
        said = super().release(agent, function, notes, project, not_c, claim_token=claim_token, handoff=handoff,
                               room_evidence=room_evidence)
        fn = self._function(function, project)
        self.rangechecks.gave_up(self.project_store(fn['project_id']).one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn)
        return said

    @release_operation("function", protect_new=True)
    def release_function(self, agent: Agent, function: str, notes: str, project: str | None = None,
                         needs: str = "", not_c: bool = False, move_on: bool = False, claim_token: str = "", *,
                         next_experiment=None, automatic_pivot: bool | None = None) -> str:
        """release_function as agents call it (the app's own releases and a room host's go straight to release).
        On a demo fork and a large x86 game, agents released functions seconds after claiming them, untried,
        for reasons a compile would have shown were none (12,565 releases, median 5 s after the claim): so a release
        before compile_and_diff's move-on rule says so is answered once with what to do instead, and goes through
        when asked again. `needs`: a direct callee in original assembly which blocks this work. It is recorded and may be
        claimed once per unchanged input, respecting exhaustion pauses; inline/header concerns stay notes.
        A release without a compile doesn't count against the session's claims, and says so. `not_c`: the agent's
        verdict that no C makes it (Network.release), which isn't pushed back: it's a finding, not giving up, and
        it counts only once agents of two models agree."""
        from ...search_notes import boilerplate

        if boilerplate(notes):
            notes = ''
        if move_on and not notes.strip():
            raise NetworkError("Explain what you tried and why you need to move on.")
        fn = self._function(function, project)
        if not not_c and not needs.strip() and not self.project_store(fn['project_id']).one(
                'SELECT 1 FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id):
            # A local pass can release on its move-on limit before the model sends its findings.
            # Notes need no lease; never release or change another worker's claim here.
            if notes.strip():
                self.add_note(agent, project, notes, fn['name'])
            return f"{fn['name']} is already released from your claims. " + (
                'Findings saved. ' if notes.strip() else '') + 'Claim another eligible function.'
        need = self._prerequisite(agent, fn, needs, project) if needs.strip() else None
        progress = self.claim_progress(agent, fn)
        if not move_on and not not_c and need is None and progress is not None and \
                (push := self._push_back(agent, fn, progress, project)):
            return push
        if notes.strip() and progress is not None and not progress['compiles'] and not not_c:
            # A small model released a function it never compiled, with move_on and notes saying "no better score
            # in 6 compiles", and those notes are what the next agent starts from. Say what the app knows.
            notes = '[Released without compiling it once; notes not checked by a compile.] ' + notes
        handoff = None
        stale_handoff = False
        if next_experiment is not None and not not_c and progress is not None:
            from ..agent_review import StaleHandoff, prepare_experiment

            slug = self.project_store(fn['project_id']).one('SELECT slug FROM projects WHERE id=?', fn['project_id'])['slug']
            try:
                handoff = prepare_experiment(self, agent, slug, fn, next_experiment, progress)
            except StaleHandoff:
                stale_handoff = True
                findings = [next_experiment.get(field, '').strip() for field in ('tried', 'changed')]
                notes = '\n'.join(dict.fromkeys(filter(None, [notes.strip(), *findings])))
        said = self.release(agent, function, notes, project, not_c, claim_token=claim_token, handoff=handoff)
        if handoff is not None:
            said += '\nNext experiment saved with the handoff. The next claim will show it while applicable.'
        elif stale_handoff:
            said += '\n'
            if notes.strip():
                said += 'Findings saved as notes. '
            said += 'The proposed experiment referred to a different candidate revision.'
        if not not_c and need is None and progress is not None and progress['compiles']:
            from .. import release_repairs
            slug = self.project_store(fn['project_id']).one('SELECT slug FROM projects WHERE id=?',fn['project_id'])['slug']
            plateau = large_plateau(fn) and progress['no_better'] >= progress['after']
            said += release_repairs.handoff(self, slug, self._function(fn['name'], slug), plateau=plateau)
        if need is not None:
            self.wait_for(agent, fn, need)
            said += (f"\n{fn['name']} now waits for {need['name']}: it's claimed after everything else until "
                     f"{need['name']} matches, and {need['name']} goes first meanwhile.")
            if automatic_pivot is not False:
                said += self._pivot(agent, project, need, fn)
        if automatic_pivot is not None:
            return said  # The assignment response says what comes after this release.
        if progress is not None and not progress["compiles"]:
            said += ("\nYou didn't compile it, so this release doesn't count against your session's claims: claim "
                     "another one.")
        elif need is None:  # agents stopped after 2 or 3 of 4 claims because what's left looked hard
            said += ("\nClaim the next function now, until you've done your session's full count of claims: the "
                     "functions left are the hard ones, and that's no reason to stop early.")
        return said

    def _pivot(self, agent: Agent, project: str | None, need, caller=None) -> str:
        """The function a release said it needs, claimed for the agent when it's free: what to tell it."""
        from .. import dependency_guard
        if caller is not None and not dependency_guard.permitted(self,caller,need,agent):
            return '\nDependency pivot skipped: paused or already investigated for these inputs. Claim another eligible function.'
        fork = self.fork_slug(project)
        try:
            if self._link(project) is not None:  # a room's host hands functions out
                claimed = self.claim(agent, project, need['name'])
                if caller is not None:
                    dependency_guard.remember(self,caller,need)
                return f" You hold {need['name']} now:\n\n{claimed}"
            row = self.claim_row(agent, project, need["name"])
            if caller is not None:
                dependency_guard.remember(self,caller,need)
        except NetworkError as e:
            return f" ({e}: claim another function meanwhile.)"
        try:
            brief = self.forks.brief(row, agent, "CLAIMED") if fork else self._brief(row, agent, "CLAIMED")
        except Exception:  # the claim stands; the brief is one call away
            brief = f"(claim_function(function=\"{need['name']}\") shows its brief.)"
        return f" You hold {need['name']} now:\n\n{brief}"

    def _prerequisite(self, agent: Agent, fn, needs: str, project: str | None):
        """The function a release says this one needs first, checked: another open function of the project."""
        try:
            need = self._function(self.resolve_function(agent, project, needs.strip()), project)
        except NetworkError as e:
            raise NetworkError(f"needs: {e} (give the function this one needs first, by name or address)") from None
        if need["id"] == fn["id"]:
            raise NetworkError("needs: a function can't wait for itself")
        if need["status"] == "matched":
            raise NetworkError(f"needs: {need['name']} already matches, so it's there to use (get_function shows its "
                               "code). If the call to it still differs, its declaration is what to fix, in this "
                               "claim; nothing was released.")
        if need["status"] != "open":
            raise NetworkError(f"needs: {need['name']} is {need['status']} (no agent works on it), so this one would "
                               "wait forever; nothing was released.")
        seen, at = {need["id"]}, need["id"]
        while (row := self.project_store(fn['project_id']).one("SELECT needs_id FROM waits WHERE function_id=?", at)) is not None:
            at = row["needs_id"]
            if at == fn["id"]:  # waiting for one that (in the end) waits for this one: they'd both wait for good
                raise NetworkError(
                    f"needs: {need['name']} already waits for {fn['name']}, so they depend on each other (a caller "
                    "and its callee, each one's convention coming from the other): work on them together instead. "
                    f"compile_and_diff(function=\"{fn['name']}, {need['name']}\", source=both definitions) judges both "
                    "in one build; nothing was released.")
            if at in seen:
                break
            seen.add(at)
        from .. import dependency_guard
        dependency_guard.validate(self,fn,need,agent)
        return need

    def _push_back(self, agent: Agent, fn, progress: dict, project: str | None) -> str | None:
        """What a release before the move-on rule gets instead; None lets it through. One rule, compile_and_diff's
        (stall_status: the same count, the same number): a release goes through once the function's move-on count is
        reached, with no compile more. Before that it's pushed back once per claim, with where the agent stands; asked
        again, it goes through. A release with no compile at all is always pushed back (agents called
        release_function twice to get past it untried). An agent whose own instructions said it was out of tries
        wouldn't compile and asked again and again, 7 times in 90 seconds, each answer the full advice, until the
        user's quota ran out: after UNCOMPILED_PUSH_BACKS it's told in one line to end its session instead (the
        function stays held until the claim lapses, so it still isn't passed around untried). A function compiling
        can't help with (release_exempt, _rom_release_exempt) goes through whenever it's asked, also after those: a
        boundary report filed after two push-backs says itself to release it. It used to let a
        pushed-back release through after exactly one more compile, which agents then spent on nothing (446
        push-backs, 36 improved after it)."""
        if fn["status"] != "open" or agent.name in MACHINE_AGENTS:
            return None
        n, streak, after = progress["compiles"], progress["no_better"], progress["after"]
        if n and streak >= after:
            return None  # compile_and_diff said to move on
        if progress.get("handoff"):
            return None  # the hand-off clock ran out on a near-match (network.HANDOFF_MINUTES)
        noted = progress.get("handoff_clock") and self.project_store(fn['project_id']).one(
            "SELECT 1 FROM notes WHERE function_id=? AND agent_id=? AND created_at >= ?",
            fn["id"], agent.id, progress["claimed_at"])
        if noted:
            return None  # the clock allows a hand-off (any score) and its note was written: the skill's rule, not the count
        pushed = self.project_store(fn['project_id']).one("SELECT COUNT(*) AS n FROM events WHERE agent_id=? AND kind='release_pushed' AND "
                                "detail=? AND created_at >= ?", agent.id, fn["name"], progress["claimed_at"])["n"]
        if n and pushed:
            return None  # asked again after the push-back, having tried it: it goes through
        fork = self.fork_slug(project)
        try:
            exempt = self.forks.release_exempt(fork, fn) if fork else self._rom_release_exempt(fn)
            hints = self.forks.release_hints(fork, fn) if fork else []
        except (NetworkError, OSError, ValueError, KeyError):
            exempt, hints = None, []
        if exempt:
            return None  # compiling can't help with it (it may have become so since the last ask)
        if not n and pushed >= UNCOMPILED_PUSH_BACKS:  # it won't compile it: asking again only spends the user's quota
            with self.project_store(fn['project_id']).tx() as db:
                self._event(db, fn["project_id"], agent.id, "release_pushed", fn["name"])
            return (f"Not released yet: you haven't compiled {fn['name']} since you claimed it, and asking again "
                    f"won't release it. Compile it now, or end your session here: don't call release_function again.")
        with self.project_store(fn['project_id']).tx() as db:
            self._event(db, fn["project_id"], agent.id, "release_pushed", fn["name"])
        head = (f"Not released yet: you haven't compiled {fn['name']} since you claimed it." if not n else
                f"Not released yet: {n} compile{'s' if n != 1 else ''} of {fn['name']} since you claimed it, "
                f"{stall_line(streak, after)}.")
        x86 = self.console(self.project_store(fn['project_id']).one("SELECT * FROM projects WHERE id=?", fn["project_id"])).arch == "x86"
        from .. import agent_tokens
        source = agent_tokens.candidate(self, agent, project, fn['name'])[0] if n else None
        lines = [head,
                 f"No-improvement budget remaining: {max(0,after-streak)}/{after}; improvements reset it.",
                 agent_tokens.next_action(self, agent, fn, source=source),
                 "Compile the next step your notes name first, and act on what the diff shows"
                 + (": a target that reads `this` or a pointer from the stack ([esp+4]) and ends in `ret N` is a "
                    "function taking it as a stack parameter (__stdcall; __cdecl with a plain `ret`), not a member "
                    "function; extra or missing pushes or register loads around a call mean the callee's declaration "
                    "(parameters, convention) differs" if x86 else "")
                 + "; fix structure and calls first, registers last.",
                 *hints,
                 *(["The hand-off clock has run out: write a hand-off note with add_note (edits tried with scores, rows "
                    "left, next idea), then release_function again; it goes through."] if progress.get("handoff_clock") else []),
                 ("Compile it (a release with no compile isn't taken)." if not n else
                  f"release_function goes through at {after} compiles in a row without a better score, or if you "
                  "ask again now with notes saying why it can't get closer.")
                 + " If it needs another function done first, release_function(..., needs=\"<that function>\") "
                 "records that, puts that one first in the queue and hands it to you."]
        return "\n".join(lines)

    def _rom_release_exempt(self, fn) -> str | None:
        """A ROM project's function compiling can't help with: one with a boundary report waiting for the user."""
        proj = self.project_store(fn['project_id']).one("SELECT config FROM projects WHERE id=?", fn["project_id"])
        if any(r.get("status") == "open" and r.get("function") == fn["name"] for r in self.cfg(proj).get("boundaries", [])):
            return "a boundary report waits for the user"
        return None

    def add_spend(self, slug: str, usd: float, tokens: int) -> None:
        if usd or tokens:
            def change(cfg):
                sp = cfg.setdefault("spend", {"usd": 0.0, "tokens": 0})
                sp["usd"] = round(sp.get("usd", 0) + usd, 6)
                sp["tokens"] = sp.get("tokens", 0) + tokens
            # Usage changes no source files; never queue every agent behind a progress scan.
            self._update_cfg(slug, change, synchronize=False)

    def spend(self, slug: str) -> dict | None:
        return self.cfg(self._project(slug)).get("spend")

    @timed_build
    @trial
    def _judge_with(self, proj, fn, toolchain: str, flags: list[str], source: str, *, evidence=False):
        """(matched, diff or None, log) for C built with this compiler and flags, judged like an attempt, recording
        nothing. A queued score-only judge can request aligned evidence in place of a rendered diff. Advisory checks stay in `_last_judge_checks` for the caller that wants them (`jump_tables`:
        the cases whose compiled table entry differs, when every instruction is exact and only the table's data is not)."""
        self._last_judge_checks = {}
        from ...compiler import compile_source
        from ...project import compiler_config
        from .. import compiler_flags
        if rejected := compiler_flags.known(self,toolchain,flags):
            return False,None,rejected
        header = self._context(proj["id"], fn["unit"])["header"]
        settings = {"toolchain": toolchain, "flags": flags, "timeout": 20}
        invocation = compiler_config(Path(proj['root']), {'compiler': settings})
        invocation.identity = (proj['id'], fn['id'], fn['name'], fn['target'], fn['relocs'])
        result = compile_source(invocation, header, canonical_source(fn, source))
        if not result.ok:
            compiler_flags.remember(self,toolchain,flags,result.log)
            return False, None, result.log
        # The queue consumes a score and link problems; full diagnostics would analyse the same table again.
        options = {"diagnostics": False} if evidence else {}
        cand, verdict, problems = self._evaluate(fn, proj, result.obj, **options)
        if cand is None:
            return False, None, "the function isn't in what it compiled to"
        compare = scoring.score_evidence if evidence else scoring.diff
        d = compare(self._target_obj(fn, proj), cand)
        matched = verdict if verdict is not None else d.bytes_match
        if not evidence and not matched and proj['arch'] == 'mips':
            self._last_judge_checks = dict(self._candidate_checks(fn, proj, result.obj))
        return matched, d, "\n".join(problems)

    def _source_of(self, fn) -> str | None:
        """A function's best C, else its latest attempt."""
        if fn['best_source']:
            return canonical_source(fn, fn['best_source'])
        row = self.project_store(fn['project_id']).one("SELECT source FROM attempts WHERE function_id=? AND (source_hash IS NOT NULL OR source IS NOT NULL) ORDER BY id DESC "
                             "LIMIT 1", fn["id"])
        source = fn["best_source"] or (row["source"] if row else None)
        return canonical_source(fn, source) if source else None

    def claim_row(self, agent: Agent, project: str | None = None, function: str | None = None, strategy: str = "easiest"):
        function = self.focus_target(agent,project,function)
        policy = self.selection(project, agent.name) if project else {}
        # a starred function goes first on ROM and fork projects alike (#290)
        if function is None and project and not any(v is not None and v != "" for v in policy.values()):
            proj = self._project(project)
            for name in self.cfg(proj).get("priority", []):
                row = self.project_store(proj['id']).one("SELECT f.id, f.status FROM functions f WHERE f.project_id=? AND f.name=?", proj["id"], name)
                held = self.project_store(proj['id']).one("SELECT 1 FROM claims WHERE agent_id=?", agent.id)
                if row and row["status"] == "open" and not held and not self.project_store(proj['id']).one(
                        "SELECT 1 FROM claims WHERE function_id=?", row["id"]):
                    function = name
                    break
        self._yield_autopilot(function, project)
        return Network.claim_row(self, agent, project, function, strategy)
