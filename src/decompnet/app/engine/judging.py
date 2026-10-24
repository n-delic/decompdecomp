"""The judge and the work around it: each function's target (its bytes as the ROM has them), claims, attempts
(compile, link at the function's address, compare), batches, giving a function up, and what agents spend.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ... import scoring
from ...network import MACHINE_AGENTS, WAITING, Agent, Network, NetworkError, stall_line
from ...rom import match, split
from ...rom.symbols import SymbolTable
from .common import _AUTO_TOKEN, BATCH_INSNS


class JudgeMixin:
    def _rom_target(self, fn) -> match.RomTarget:
        cfg = self.cfg(self.store.one("SELECT config FROM projects WHERE id=?", fn["project_id"]))
        return match.RomTarget(fn["name"], fn["vram"], fn["target"], match.RomTarget.relocs_from_json(json.loads(fn["relocs"])),
                               cfg.get("big_endian", True), cfg.get("gp", 0))

    def _make_target(self, proj, rf: split.RomFunction, syms: SymbolTable) -> match.RomTarget:
        cfg = self.cfg(proj)
        return match.target_from_split(rf, syms.resolve, cfg.get("big_endian", True), cfg.get("gp", 0))

    def _target_obj(self, fn, proj):
        if not self.is_rom(proj):
            return super()._target_obj(fn, proj)
        return match.canonical_target(self._rom_target(fn), self.symbols(proj["id"]))

    def _evaluate(self, fn, proj, obj: bytes):
        if not self.is_rom(proj):
            return super()._evaluate(fn, proj, obj)
        res = match.link(obj, fn["name"], self._rom_target(fn), self.symbols(proj["id"]), self.reader(proj))
        return res.candidate, res.exact if res.candidate is not None else None, res.problems

    def rename_tokens(self, proj, text: str) -> str:
        """Show automatic names under their current names (func_80001234 -> deflate)."""
        syms = self.symbols(proj["id"])
        return _AUTO_TOKEN.sub(lambda m: syms.label(int(m.group(0)[-8:], 16)), text)

    def claim(self, agent: Agent, project: str | None = None, function: str | None = None, strategy: str = "easiest") -> str:
        if (fork := self.fork_slug(project)):
            return self.forks.claim(agent, fork, function, strategy)
        link = self._link(project)
        if link is None:
            return super().claim(agent, project, function, strategy)
        if function:
            function = self._function(function, project)["name"]
        name = link.claim(agent, function, strategy)
        if name is None:
            return "Nothing left to claim in this room — every open function is matched or someone is on it. 🎉"
        link.sync()
        fn = self.claim_row(agent, project, name, strategy)
        return self._brief(fn, agent, header="CLAIMED")

    def claim_many(self, agent: Agent, project: str | None, count: int, strategy: str = "easiest") -> str:
        """Several small functions at once (count 2-6): the next one by `strategy` and, when it's small, its
        neighbours in the same file. Most small functions match on the first try, so an agent writes them all and
        judges them in one compile_and_diff: a fraction of the turns. Big functions come alone."""
        count = max(1, min(int(count or 1), 6))
        if count == 1 or self._link(project) is not None:
            return self.claim(agent, project, None, strategy)
        proj = self._project(project)
        held = self.store.all("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? "
                              "AND f.project_id=? ORDER BY f.vram, f.id", agent.id, proj["id"])
        if not held:
            text = self.claim(agent, project, None, strategy)  # the first, the usual way (with its "nothing left")
            held = self.store.all("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? "
                                  "AND f.project_id=?", agent.id, proj["id"])
            if not held or held[0]["n_insns"] > BATCH_INSNS:
                return text
        first = held[0]
        rows = list(held)
        if first["n_insns"] <= BATCH_INSNS and len(rows) < count:
            rows += self._batch_more(agent, proj, first, count - len(rows))
        if len(rows) == 1:
            return self.claim(agent, project, None, strategy)
        names = [r["name"] for r in rows]
        fork = self.fork_slug(project)
        head = (f"## CLAIMED {len(rows)} small functions: {', '.join(names)}\n"
                f"Write all of them, then judge them in one call: compile_and_diff(function=\"{', '.join(names)}\", "
                f"source=<all {len(rows)} definitions>). Each is judged on its own and saved as soon as it matches; "
                "then fix what's left (one at a time is fine). release_function any you can't do.")
        briefs = [self.forks.brief(r, agent, "CLAIMED") if fork else self._brief(r, agent, "CLAIMED") for r in rows]
        return "\n\n---\n\n".join([head, *briefs])

    def _batch_more(self, agent: Agent, proj, first, n: int) -> list:
        """Up to n more small open functions next to `first` (same file, nearest address), claimed for the agent."""
        now = time.time()
        with self.store.tx() as db:
            self._expire_claims(db)
            rows = db.execute(
                "SELECT f.* FROM functions f WHERE f.project_id=? AND f.status='open' AND f.boost >= 0 AND f.n_insns <= ? "
                f"AND f.abandons = 0 AND f.unit=? AND f.id NOT IN (SELECT function_id FROM claims) AND f.id NOT IN ({WAITING}) "
                "ORDER BY ABS(COALESCE(f.vram, 0) - ?), f.id LIMIT ?",
                (proj["id"], BATCH_INSNS, first["unit"], first["vram"] or 0, n)).fetchall()
            for r in rows:
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (r["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, proj["id"], agent.id, "claim", r["name"])
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
            except NetworkError as e:
                res = {"text": f"❌ {e}", "matched": False}
            matched += bool(res.get("matched"))
            out.append(f"### {name}\n{res['text']}")
        return f"{matched} of {len(names)} matched.\n\n" + "\n\n".join(out)

    def _yield_autopilot(self, function: str | None, project: str | None) -> None:
        """The autopilot holds a function only while its draft compiles: an agent that wants it waits for that."""
        if not function:
            return
        try:
            fid = self._function(function, project)["id"]
        except NetworkError:
            return
        for _ in range(100):
            held = self.store.one("SELECT a.name FROM claims c JOIN agents a ON a.id=c.agent_id WHERE c.function_id=?", fid)
            if not held or held["name"] != "autopilot":
                return
            time.sleep(0.2)

    def attempt(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        fn = self._function(function, project)
        slug = self.store.one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"]
        with self.progress.judging(slug):
            return self._attempt(agent, function, source, project)

    def _record(self, fn, *args, **kwargs):
        with self.progress.lock:
            if not self.progress.applying:
                slug = self.store.one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"]
                self.progress.sync(slug, force=True)
            self.progress.check_result()
            return super()._record(fn, *args, **kwargs)

    def _attempt(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        self._yield_autopilot(function, project)
        if (fork := self.fork_slug(project)):
            return self.forks.attempt(agent, fork, function, source)
        link = self._link(project)
        before = self._function(function, project)["best_score"] if link else None
        res = super().attempt(agent, function, source, project)
        if link is None or res["score"] is None:
            return res
        name = self._function(function, project)["name"]
        if res["matched"]:
            verdict = link.attempt(agent, name, source)
            if not verdict.get("matched"):
                res["text"] += ("\n⚠️ The room's host could not confirm this match on their copy (score "
                                f"{verdict.get('score')}). Check that both of you use the same compiler settings.")
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

    def apply_reported_best(self, slug: str, name: str, score: int, source: str, agent: Agent) -> None:
        """Host side: a room member's better attempt, as they report it (their own compile scored it)."""
        fn = self._function(name, slug)
        if fn["status"] != "open" or score <= 0 or (fn["best_score"] is not None and score >= fn["best_score"]):
            return
        self._record(fn, agent, score, source, self._context(fn["project_id"], fn["unit"])["version"], via="room")

    def release(self, agent: Agent, function: str, notes: str, project: str | None = None, not_c: bool = False) -> str:
        if (fork := self.fork_slug(project)):
            return self.forks.release(agent, fork, function, notes, not_c)
        link = self._link(project)
        if link is not None:
            link.call("/collab/release", agent, function=self._function(function, project)["name"], notes=notes,
                      not_c=not_c)
        said = super().release(agent, function, notes, project, not_c)
        fn = self._function(function, project)
        self.rangechecks.gave_up(self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn)
        return said

    def release_function(self, agent: Agent, function: str, notes: str, project: str | None = None,
                         needs: str = "", not_c: bool = False) -> str:
        """release_function as agents call it (the app's own releases and a room host's go straight to release).
        On a demo fork and a large x86 game, agents released functions seconds after claiming them, untried,
        for reasons a compile would have shown were none (12,565 releases, median 5 s after the claim): so a release
        before compile_and_diff's move-on rule says so is answered once with what to do instead, and goes through
        when asked again. `needs`: another function this one needs done first (a callee whose convention or body it
        depends on, an inlined helper): recorded (it waits, that one goes first) and claimed for the agent when free.
        A release without a compile doesn't count against the session's claims, and says so. `not_c`: the agent's
        verdict that no C makes it (Network.release), which isn't pushed back: it's a finding, not giving up, and
        it counts only once agents of two models agree."""
        fn = self._function(function, project)
        need = self._prerequisite(agent, fn, needs, project) if needs.strip() else None
        progress = self.claim_progress(agent, fn)
        if not not_c and need is None and progress is not None and \
                (push := self._push_back(agent, fn, progress, project)):
            return push
        said = self.release(agent, function, notes, project, not_c)
        if need is not None:
            self.wait_for(agent, fn, need)
            said += (f"\n{fn['name']} now waits for {need['name']}: it's claimed after everything else until "
                     f"{need['name']} matches, and {need['name']} goes first meanwhile.")
            said += self._pivot(agent, project, need)
        if progress is not None and not progress["compiles"]:
            said += ("\nYou didn't compile it, so this release doesn't count against your session's claims: claim "
                     "another one.")
        elif need is None:  # agents stopped after 2 or 3 of 4 claims because what's left looked hard
            said += ("\nClaim the next function now, until you've done your session's full count of claims: the "
                     "functions left are the hard ones, and that's no reason to stop early.")
        return said

    def _pivot(self, agent: Agent, project: str | None, need) -> str:
        """The function a release said it needs, claimed for the agent when it's free: what to tell it."""
        fork = self.fork_slug(project)
        try:
            if self._link(project) is not None:  # a room's host hands functions out
                return f" You hold {need['name']} now:\n\n{self.claim(agent, project, need['name'])}"
            row = self.claim_row(agent, project, need["name"])
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
        while (row := self.store.one("SELECT needs_id FROM waits WHERE function_id=?", at)) is not None:
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
        return need

    def _push_back(self, agent: Agent, fn, progress: dict, project: str | None) -> str | None:
        """What a release before the move-on rule gets instead; None lets it through. One rule, compile_and_diff's
        (stall_status: the same count, the same number): a release goes through once the function's move-on count is
        reached, with no compile more. Before that it's pushed back once per claim, with where the agent stands; asked
        again, it goes through. A release with no compile at all is always pushed back (agents called
        release_function twice to get past it untried). It used to let a pushed-back release through after exactly one
        more compile, which agents then spent on nothing (446 push-backs, 36 improved after it)."""
        if fn["status"] != "open" or agent.name in MACHINE_AGENTS:
            return None
        n, streak, after = progress["compiles"], progress["no_better"], progress["after"]
        if n and streak >= after:
            return None  # compile_and_diff said to move on
        pushed = self.store.one("SELECT 1 FROM events WHERE agent_id=? AND kind='release_pushed' AND detail=? AND "
                                "created_at >= ?", agent.id, fn["name"], progress["claimed_at"])
        if n and pushed is not None:
            return None  # asked again after the push-back, having tried it: it goes through
        fork = self.fork_slug(project)
        try:
            exempt = self.forks.release_exempt(fork, fn) if fork else self._rom_release_exempt(fn)
            hints = self.forks.release_hints(fork, fn) if fork else []
        except (NetworkError, OSError, ValueError, KeyError):
            exempt, hints = None, []
        if exempt:
            return None
        with self.store.tx() as db:
            self._event(db, fn["project_id"], agent.id, "release_pushed", fn["name"])
        head = (f"Not released yet: you haven't compiled {fn['name']} since you claimed it." if not n else
                f"Not released yet: {n} compile{'s' if n != 1 else ''} of {fn['name']} since you claimed it, "
                f"{stall_line(streak, after)}.")
        x86 = self.console(self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])).arch == "x86"
        lines = [head,
                 "Compile the next step your notes name first, and act on what the diff shows"
                 + (": a target that reads `this` or a pointer from the stack ([esp+4]) and ends in `ret N` is a "
                    "function taking it as a stack parameter (__stdcall; __cdecl with a plain `ret`), not a member "
                    "function; extra or missing pushes or register loads around a call mean the callee's declaration "
                    "(parameters, convention) differs" if x86 else "")
                 + "; fix structure and calls first, registers last.",
                 *hints,
                 ("Compile it (a release with no compile isn't taken)." if not n else
                  f"release_function goes through at {after} compiles in a row without a better score, or if you "
                  "ask again now with notes saying why it can't get closer.")
                 + " If it needs another function done first, release_function(..., needs=\"<that function>\") "
                 "records that, puts that one first in the queue and hands it to you."]
        return "\n".join(lines)

    def _rom_release_exempt(self, fn) -> str | None:
        """A ROM project's function compiling can't help with: one with a boundary report waiting for the user."""
        proj = self.store.one("SELECT config FROM projects WHERE id=?", fn["project_id"])
        if any(r.get("status") == "open" and r.get("function") == fn["name"] for r in self.cfg(proj).get("boundaries", [])):
            return "a boundary report waits for the user"
        return None

    def add_spend(self, slug: str, usd: float, tokens: int) -> None:
        if usd or tokens:
            def change(cfg):
                sp = cfg.setdefault("spend", {"usd": 0.0, "tokens": 0})
                sp["usd"] = round(sp.get("usd", 0) + usd, 6)
                sp["tokens"] = sp.get("tokens", 0) + tokens
            self._update_cfg(slug, change)

    def spend(self, slug: str) -> dict | None:
        return self.cfg(self._project(slug)).get("spend")

    def _judge_with(self, proj, fn, toolchain: str, flags: list[str], source: str):
        """(matched, diff or None, log) for C built with this compiler and flags, judged like an attempt, recording
        nothing."""
        from ...compiler import compile_source
        from ...project import compiler_config

        header = self._context(proj["id"], fn["unit"])["header"]
        settings = {"toolchain": toolchain, "flags": flags, "timeout": 20}
        result = compile_source(compiler_config(Path(proj["root"]), {"compiler": settings}), header, source)
        if not result.ok:
            return False, None, result.log
        cand, verdict, _ = self._evaluate(fn, proj, result.obj)
        if cand is None:
            return False, None, "the function isn't in what it compiled to"
        d = scoring.diff(self._target_obj(fn, proj), cand)
        return (verdict if verdict is not None else d.bytes_match), d, ""

    def _source_of(self, fn) -> str | None:
        """A function's best C, else its latest attempt."""
        row = self.store.one("SELECT source FROM attempts WHERE function_id=? AND source IS NOT NULL ORDER BY id DESC "
                             "LIMIT 1", fn["id"])
        return fn["best_source"] or (row["source"] if row else None)

    def claim_row(self, agent: Agent, project: str | None = None, function: str | None = None, strategy: str = "easiest"):
        if function is None and project and not self.fork_slug(project):
            proj = self._project(project)
            for name in self.cfg(proj).get("priority", []):
                row = self.store.one("SELECT f.id, f.status FROM functions f WHERE f.project_id=? AND f.name=?", proj["id"], name)
                held = self.store.one("SELECT 1 FROM claims WHERE agent_id=?", agent.id)
                if row and row["status"] == "open" and not held and not self.store.one(
                        "SELECT 1 FROM claims WHERE function_id=?", row["id"]):
                    function = name
                    break
        self._yield_autopilot(function, project)
        return Network.claim_row(self, agent, project, function, strategy)
