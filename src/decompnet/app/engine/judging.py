"""The judge and the work around it: each function's target (its bytes as the ROM has them), claims, attempts
(compile, link at the function's address, compare), batches, giving a function up, and what agents spend.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ... import scoring
from ...network import Agent, Network, NetworkError
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
                "AND f.abandons = 0 AND f.unit=? AND f.id NOT IN (SELECT function_id FROM claims) "
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

    def release(self, agent: Agent, function: str, notes: str, project: str | None = None) -> str:
        if (fork := self.fork_slug(project)):
            return self.forks.release(agent, fork, function, notes)
        link = self._link(project)
        if link is not None:
            link.call("/collab/release", agent, function=self._function(function, project)["name"], notes=notes)
        said = super().release(agent, function, notes, project)
        fn = self._function(function, project)
        self.rangechecks.gave_up(self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn)
        return said

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
