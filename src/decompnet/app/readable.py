"""Matched code made readable: real names, types and comments, and still byte for byte.

C that matches is often hard to read: func_002C73A8, arg0, var_v1, casts and odd orders that only exist so the
compiler emits the same bytes. People who port or mod a game need code they understand. So helpers can work on
matched functions too: rename what they can tell (rename_symbol), describe structs and constants in the shared header
(edit_context), and rewrite the function with good names, types and a comment saying what it does. A rewrite counts
only when the function still matches byte for byte; otherwise it's refused and the matching C stays. The project
shows two numbers: matched, and readable.

In project mode (GameCube, Wii, PC, DS, public decomps) the matching C is the project's own source file: a rewrite is
judged as any compile of a matched function is there (ForkEngine.attempt): kept and committed only when it still
matches and the file's other matched functions do too, else the file is put back as it was. The decomp is never left
broken by polishing.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import scoring
from ..forks.project import ForkProject
from ..network import GAME_CODE, Agent, NetworkError

CLAIM_SECONDS = 30 * 60
MAX_INSNS = 400  # bigger functions wait: a readable rewrite of 2,000 instructions is a project of its own
# stubs and one-liners have nothing to explain (their names come with the functions that call them): polishing
# smallest first spent a session on empty functions in Mario Kart 64
MIN_INSNS = 8
# what polishing takes (claim) and what a polishing helper's session counts as left (agents.Manager._left): the game's
# own matched code that isn't readable yet, of a size worth explaining
TODO = f"status='matched' AND readable=0 AND {GAME_CODE} AND n_insns BETWEEN {MIN_INSNS} AND {MAX_INSNS}"


class Readability:
    def __init__(self, net):
        self.net = net

    def _matching_c(self, proj, fn) -> str:
        """The C a matched function matches with: the database's in a ROM project, its source file's in a fork."""
        if self.net.is_rom(proj):
            return fn["matched_source"] or ""
        return (self.net.forks._current_source(ForkProject(Path(proj["root"])), fn, comment=True)
                or fn["matched_source"] or "")

    def counts(self, proj) -> dict:
        """The game's own code only (GAME_CODE): library code set aside is out of progress, so out of polishing too."""
        row = self.net.store.one("SELECT SUM(status='matched') AS matched, SUM(status='matched' AND readable=1) AS readable "
                                 f"FROM functions WHERE project_id=? AND {GAME_CODE}", proj["id"])
        return {"matched": row["matched"] or 0, "readable": row["readable"] or 0}

    def left(self, proj) -> int:
        """How many functions polishing can still take, held ones included: a polishing helper stops at none (a game
        with nothing left to decompile stopped its polishing helpers before they began)."""
        return self.net.store.one(f"SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND {TODO}", proj["id"])["n"]

    def claim(self, agent: Agent, slug: str) -> str:
        """The next matched function that isn't readable yet, held for this agent, with what it needs to know."""
        proj = self.net._project(slug)
        fork = not self.net.is_rom(proj)
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            held = db.execute("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                              (agent.id,)).fetchone()
            fn = held if held is not None and held["status"] == "matched" else None
            if held is not None and fn is None:
                raise NetworkError(f"you hold {held['name']}; match it or release_function it first")
            if fn is None:
                # starred first, then the biggest that fit: that's where the logic people want to read is. The game's
                # own code: an Xbox game's matched SDK functions, set aside, would have been polished with its 33
                fn = db.execute(f"SELECT * FROM functions WHERE project_id=? AND {TODO} "
                                "AND id NOT IN (SELECT function_id FROM claims) "
                                "ORDER BY boost DESC, n_insns DESC LIMIT 1", (proj["id"],)).fetchone()
                if fn is None:
                    return "Every matched function is readable already (or too big for now). 🎉"
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + CLAIM_SECONDS))
                self.net._event(db, proj["id"], agent.id, "polish", fn["name"])  # not "claim": the feed and routing tell them apart
        counts = self.counts(proj)
        where = f" at 0x{fn['vram']:08X}" if fn["vram"] is not None else f" in {fn['unit']}"
        names = ("- rename_symbol the function, and the globals and functions it uses, once their purpose is clear "
                 "(read_file and search_code show what it calls and what calls it);\n"
                 "- describe structs, enums and constants in the project's headers (edit_file) and use their names here;\n"
                 if fork else
                 "- rename_symbol the function, and the globals and functions it uses, once their purpose is clear;\n"
                 "- describe structs, enums and constants in the shared header (edit_context) and use their names here;\n")
        return (f"MAKE READABLE: {fn['name']}{where}, {fn['n_insns']} instructions "
                f"({counts['readable']} of {counts['matched']} matched functions are readable so far).\n\n"
                f"It matches today with this C:\n```c\n{self._matching_c(proj, fn).strip()}\n```\n\n"
                "Make it readable without changing what the compiler makes of it:\n" + names +
                "- give parameters and locals meaningful names; write one comment saying what the function does;\n"
                "- keep the statements' shape: the order, the casts and the temporaries that make it match.\n"
                f"Then make_readable(\"{fn['name']}\", source). It's judged again: it must still match byte for byte, or "
                "it's refused and the matching C stays.")

    def submit(self, agent: Agent, slug: str, function: str, source: str) -> str:
        """An agent's readable rewrite of a matched function: kept only when it still matches byte for byte."""
        with self.net.progress.judging(slug):
            return self._submit(agent, slug, function, source)

    def _submit(self, agent: Agent, slug: str, function: str, source: str) -> str:
        proj = self.net._project(slug)
        fn = self.net._function(function, proj["slug"])
        if fn["status"] != "matched":
            raise NetworkError(f"{fn['name']} isn't matched yet: compile_and_diff it first")
        claim = self.net.store.one("SELECT agent_id FROM claims WHERE function_id=?", fn["id"])
        if claim is not None and claim["agent_id"] != agent.id:
            raise NetworkError(f"{fn['name']} is held by {self.net._agent_name(claim['agent_id'])}")
        if " ".join(source.split()) == " ".join(self._matching_c(proj, fn).split()):
            raise NetworkError("that's the C it has already: rename, type and comment it first")
        if not self.net.is_rom(proj):  # the project's own file, judged and committed (or put back) as any compile
            res = self.net.forks.attempt(agent, proj["slug"], fn["name"], source, comment=True)
            if not res.get("matched"):
                return res["text"]
            return self._readable(proj, fn, agent, source)
        ctx = self.net._context(proj["id"], fn["unit"])
        result = self.net._compile(proj, ctx["header"], source, fn)
        if not result.ok:
            self.net._record(fn, agent, None, source, ctx["version"])  # its compiles count, as a decompiling one's
            return f"❌ COMPILE ERROR: the matching C stays.\n{result.log.strip()}"
        cand, verdict, problems = self.net._evaluate(fn, proj, result.obj)
        if cand is None:
            self.net._record(fn, agent, None, source, ctx["version"], compiled=True)
            return f"❌ No function named `{fn['name']}` in it: keep its name (rename it with rename_symbol)."
        d = scoring.diff(self.net._target_obj(fn, proj), cand)
        self.net._record(fn, agent, d.score, source, ctx["version"])  # not as a match: it was one already
        if not (d.bytes_match if verdict is None else verdict):
            note = "\n".join(f"- {p}" for p in problems)
            return (f"❌ It no longer matches (score {d.score}): the matching C stays. Keep the statements' shape and "
                    f"change names, types and comments only.\n{d.render()}" + (f"\n{note}" if note else ""))
        return self._readable(proj, fn, agent, source)

    def _readable(self, proj, fn, agent: Agent, source: str) -> str:
        """A rewrite that still matches: it's the function's C now, and the function counts as readable."""
        with self.net.progress.lock, self.net.store.tx() as db:
            self.net.progress.check_result()
            db.execute("UPDATE functions SET matched_source=?, best_source=?, readable=1 WHERE id=?",
                       (source, source, fn["id"]))
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            self.net._event(db, proj["id"], agent.id, "readable", fn["name"])
        counts = self.counts(proj)
        return (f"✅ {fn['name']} is readable now, and still matches byte for byte ({counts['readable']} of "
                f"{counts['matched']} readable). claim_readable for the next one.")
