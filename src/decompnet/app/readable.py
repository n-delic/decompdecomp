"""Matched code made readable: real names, types and comments, and still byte for byte.

C that matches is often hard to read: func_002C73A8, arg0, var_v1, casts and odd orders that only exist so the
compiler emits the same bytes. People who port or mod a game need code they understand. So helpers can work on
matched functions too: rename what they can tell (rename_symbol), describe structs and constants in the shared header
(edit_context), and rewrite the function with good names, types and a comment saying what it does. A rewrite counts
only when the function still matches byte for byte; otherwise it's refused and the matching C stays. The project
shows two numbers: matched, and readable.

ROM projects only: a public decomp's code lives in its own files, which agents edit there.
"""

from __future__ import annotations

import time

from .. import scoring
from ..network import Agent, NetworkError

CLAIM_SECONDS = 30 * 60
MAX_INSNS = 400  # bigger functions wait: a readable rewrite of 2,000 instructions is a project of its own


class Readability:
    def __init__(self, net):
        self.net = net

    def _rom(self, slug: str):
        proj = self.net._project(slug)
        if not self.net.is_rom(proj):
            raise NetworkError("making matched code readable works in ROM projects; in a public decomp, edit its files")
        return proj

    def counts(self, proj) -> dict:
        row = self.net.store.one("SELECT SUM(status='matched') AS matched, SUM(status='matched' AND readable=1) AS readable "
                                 "FROM functions WHERE project_id=?", proj["id"])
        return {"matched": row["matched"] or 0, "readable": row["readable"] or 0}

    def claim(self, agent: Agent, slug: str) -> str:
        """The next matched function that isn't readable yet, held for this agent, with what it needs to know."""
        proj = self._rom(slug)
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            held = db.execute("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                              (agent.id,)).fetchone()
            fn = held if held is not None and held["status"] == "matched" else None
            if held is not None and fn is None:
                raise NetworkError(f"you hold {held['name']}; match it or release_function it first")
            if fn is None:
                fn = db.execute("SELECT * FROM functions WHERE project_id=? AND status='matched' AND readable=0 "
                                "AND n_insns <= ? AND id NOT IN (SELECT function_id FROM claims) ORDER BY n_insns "
                                "LIMIT 1", (proj["id"], MAX_INSNS)).fetchone()
                if fn is None:
                    return "Every matched function is readable already (or too big for now). 🎉"
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + CLAIM_SECONDS))
                self.net._event(db, proj["id"], agent.id, "claim", fn["name"])
        counts = self.counts(proj)
        return (f"MAKE READABLE: {fn['name']} at 0x{fn['vram']:08X}, {fn['n_insns']} instructions "
                f"({counts['readable']} of {counts['matched']} matched functions are readable so far).\n\n"
                f"It matches today with this C:\n```c\n{fn['matched_source'].strip()}\n```\n\n"
                "Make it readable without changing what the compiler makes of it:\n"
                "- rename_symbol the function, and the globals and functions it uses, once their purpose is clear;\n"
                "- describe structs, enums and constants in the shared header (edit_context) and use their names here;\n"
                "- give parameters and locals meaningful names; write one comment saying what the function does;\n"
                "- keep the statements' shape: the order, the casts and the temporaries that make it match.\n"
                f"Then make_readable(\"{fn['name']}\", source). It's judged again: it must still match byte for byte, or "
                "it's refused and the matching C stays.")

    def submit(self, agent: Agent, slug: str, function: str, source: str) -> str:
        """An agent's readable rewrite of a matched function: kept only when it still matches byte for byte."""
        proj = self._rom(slug)
        fn = self.net._function(function, proj["slug"])
        if fn["status"] != "matched":
            raise NetworkError(f"{fn['name']} isn't matched yet: compile_and_diff it first")
        claim = self.net.store.one("SELECT agent_id FROM claims WHERE function_id=?", fn["id"])
        if claim is not None and claim["agent_id"] != agent.id:
            raise NetworkError(f"{fn['name']} is held by {self.net._agent_name(claim['agent_id'])}")
        if " ".join(source.split()) == " ".join((fn["matched_source"] or "").split()):
            raise NetworkError("that's the C it has already: rename, type and comment it first")
        ctx = self.net._context(proj["id"], fn["unit"])
        result = self.net._compile(proj, ctx["header"], source, fn)
        if not result.ok:
            return f"❌ COMPILE ERROR: the matching C stays.\n{result.log.strip()}"
        cand, verdict, problems = self.net._evaluate(fn, proj, result.obj)
        if cand is None:
            return f"❌ No function named `{fn['name']}` in it: keep its name (rename it with rename_symbol)."
        d = scoring.diff(self.net._target_obj(fn, proj), cand)
        if not (d.bytes_match if verdict is None else verdict):
            note = "\n".join(f"- {p}" for p in problems)
            return (f"❌ It no longer matches (score {d.score}): the matching C stays. Keep the statements' shape and "
                    f"change names, types and comments only.\n{d.render()}" + (f"\n{note}" if note else ""))
        with self.net.store.tx() as db:
            db.execute("UPDATE functions SET matched_source=?, best_source=?, readable=1 WHERE id=?",
                       (source, source, fn["id"]))
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            self.net._event(db, proj["id"], agent.id, "readable", fn["name"])
        counts = self.counts(proj)
        return (f"✅ {fn['name']} is readable now, and still matches byte for byte ({counts['readable']} of "
                f"{counts['matched']} readable). claim_readable for the next one.")
