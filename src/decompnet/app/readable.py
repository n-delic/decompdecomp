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

Then each source file is tidied (project mode): once every function of the game's in it is readable, claim_readable
hands the file out with a list (the types it shares with other files in one header, fields and classes named, the
globals it still knows by address named, dead declarations gone, one naming style) and tidy_done says it's done.
Polishing one function at a time left an Xbox game's types as the first guesses were, declared again in three files,
with duplicate and unused declarations around them. Its edits go through edit_file, which keeps every match.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from .. import scoring
from ..forks.project import ForkProject
from ..network import GAME_CODE, IN_BUILD, Agent, NetworkError

CLAIM_SECONDS = 30 * 60
# The order polishing takes functions in (starred ones first): SMALL_INSNS to BIG_INSNS biggest first, where the logic
# people want to read is; then bigger ones, smallest first (a readable rewrite of 2,000 instructions is a project of
# its own, and they waited for good behind a ceiling of 400); then small ones, biggest first. Smallest first, polishing
# spent a session on empty functions in Mario Kart 64; a floor of 8 instructions then left 15 of a small Xbox game's 33
# functions with address names, constructors and getters among them, and nothing names a callee from its caller's
# polish. So every size comes, in that order.
SMALL_INSNS, BIG_INSNS = 8, 400
ORDER = (f"boost DESC, CASE WHEN n_insns > {BIG_INSNS} THEN 1 WHEN n_insns < {SMALL_INSNS} THEN 2 ELSE 0 END, "
         f"CASE WHEN n_insns > {BIG_INSNS} THEN n_insns ELSE -n_insns END")
POLISH_TRIES = 3  # a function taken up this often since it was last made readable, and still not readable, is skipped
# what polishing takes (claim) and what a polishing helper's session counts as left (agents.Manager._left): the game's
# own matched code that isn't readable yet and isn't given up on: one no rewrite kept matching was handed out again
# and again, to every helper, being the biggest. (TODO and GIVEN_UP each take one ?, the project's id, after the
# caller's own)
GIVEN_UP = ("SELECT p.detail FROM events p WHERE p.project_id=? AND p.kind='polish' AND p.id > COALESCE((SELECT "
            "MAX(r.id) FROM events r WHERE r.project_id=p.project_id AND r.kind='readable' AND r.detail=p.detail), 0) "
            f"GROUP BY p.detail HAVING COUNT(*) >= {POLISH_TRIES}")
TODO = f"status='matched' AND readable=0 AND {GAME_CODE} AND name NOT IN ({GIVEN_UP})"


def _left_as_it_is(name: str, without: str) -> str:
    """What a helper is told of a function or file it held that has had its tries: POLISH_TRIES counts every helper's
    tries, not this one's, so it says it was tried rather than you tried it (#108)."""
    return f"You held {name}; it was tried {POLISH_TRIES} times without {without}, so it's left as it is now.\n\n"


TIDY = """\
TIDY A FILE: {unit} ({source}). Every function of the game's in it is readable now; the file around them isn't
yet. Make it read like code a person wrote, one edit_file at a time: each edit must build and keep every match, or
it's undone. Find what else declares the same things with search_code (headers, other source files, a build's own
driver files) and read_file them.
1. Types: a struct or class declared in more than one file goes in one header in include/ (create it with an include
   guard), then each copy is replaced by an #include of it, one file per edit.
2. Names: fields, classes and parameters named for what the code does with them (the functions' comments say);
   code that is a class's method written as one, Class::method, where it still matches; a function or global whose
   name doesn't fit what it does or holds (read_data shows what a global holds) renamed with rename_symbol.
3. Globals and functions it still knows by address (D_..., func_...): read_data shows what a global holds (a string,
   a table, a constant); rename_symbol each once you can tell.
4. Duplicate declarations, ones nothing uses, stray stubs: gone.
5. One naming style: the one most of the file uses.
Then tidy_done("{unit}", summary): what you changed, in a sentence or two.{data}"""
# The globals a file declares, read for its tidy brief: helpers named two getters of class-name strings "static
# data" though read_data was in their brief, and in the tidy's; what each one holds, given up front, can't be missed
EXTERN_DATA = re.compile(r"^[ \t]*extern\s+(?!\"C\")[^;(){}=]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*;", re.M)
DATA_READ, DATA_SHOWN = 32, 40  # bytes read of each, globals listed at most


class Readability:
    def __init__(self, net):
        self.net = net
        self._tidying: dict[tuple[int, str], tuple[int, float]] = {}  # (project, unit) -> (agent, until)
        self._tidy_lock = threading.Lock()  # (helpers claim at once: one file, one helper)

    def _untidy(self, proj) -> list[str]:
        """The project's source files (units) to tidy, most functions first: every function of the game's in them
        readable, none tidied yet, and not given up on (POLISH_TRIES tidy jobs without tidy_done). Project mode only:
        a ROM project's C is the database's, one function at a time."""
        if self.net.is_rom(proj):
            return []
        rows = self.net.store.all(
            "SELECT unit, COUNT(*) AS n FROM functions WHERE project_id=? AND unit != '' AND "
            f"{GAME_CODE} AND {IN_BUILD} GROUP BY unit HAVING SUM(status='matched' AND readable=1) = COUNT(*) "
            "AND unit NOT IN (SELECT detail FROM events WHERE project_id=? AND kind='tidied') "
            "AND unit NOT IN (SELECT detail FROM events WHERE project_id=? AND kind='tidy' GROUP BY detail "
            f"HAVING COUNT(*) >= {POLISH_TRIES}) ORDER BY n DESC, unit", proj["id"], proj["id"], proj["id"])
        return [r["unit"] for r in rows]

    def _file_given_up(self, proj, unit: str) -> bool:
        """Whether a file was handed out POLISH_TRIES times without being tidied (rather than tidied while held)."""
        row = self.net.store.one(
            "SELECT SUM(kind='tidy') AS tried, SUM(kind='tidied') AS tidied FROM events "
            "WHERE project_id=? AND detail=? AND kind IN ('tidy', 'tidied')", proj["id"], unit)
        return (row["tried"] or 0) >= POLISH_TRIES and not row["tidied"]

    def _matching_c(self, proj, fn) -> str:
        """The C a matched function matches with: the database's in a ROM project, its source file's in a fork."""
        if self.net.is_rom(proj):
            return fn["matched_source"] or ""
        fp = ForkProject(Path(proj["root"]))
        inline = self.net.forks.inline_definition(fp, fn)  # (a header's inline: its file has only a marker)
        return (self.net.forks._current_source(fp, fn, comment=True) or (inline[1] if inline else "")
                or fn["matched_source"] or "")

    def counts(self, proj) -> dict:
        """The game's own code only (GAME_CODE): library code set aside is out of progress, so out of polishing too."""
        row = self.net.store.one("SELECT SUM(status='matched') AS matched, SUM(status='matched' AND readable=1) AS readable "
                                 f"FROM functions WHERE project_id=? AND {GAME_CODE}", proj["id"])
        return {"matched": row["matched"] or 0, "readable": row["readable"] or 0}

    def left(self, proj) -> int:
        """How many functions polishing can still take, held ones included, and files to tidy: a polishing helper
        stops at none (a game with nothing left to decompile stopped its polishing helpers before they began)."""
        return self.net.store.one(f"SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND {TODO}",
                                  proj["id"], proj["id"])["n"] + len(self._untidy(proj))

    def _tidy_job(self, agent: Agent, proj) -> tuple[str | None, str | None]:
        """A file to tidy for this agent (the one it holds, or the next nobody does), and the file it held that was let
        go, if any."""
        now = time.time()
        left = None
        with self._tidy_lock:
            self._tidying = {k: v for k, v in self._tidying.items() if v[1] > now}
            mine = next((u for (pid, u), (who, _) in self._tidying.items() if pid == proj["id"] and who == agent.id),
                        None)
            untidy = self._untidy(proj)
            if mine is not None and mine not in untidy:
                # its tries are up, or it was tidied while held: the next file, as a function given up on or made
                # readable while held is let go (#108)
                del self._tidying[(proj["id"], mine)]
                left = mine if self._file_given_up(proj, mine) else None
                mine = None
            if mine is None:
                held = {u for (pid, u) in self._tidying if pid == proj["id"]}
                mine = next((u for u in untidy if u not in held), None)
                if mine is None:
                    return None, left
            # taking a held file up again is a try too: one a helper never finished got it back every claim without a
            # try against it, so it never left and left() never reached 0, as a function did before #108
            with self.net.store.tx() as db:
                self.net._event(db, proj["id"], agent.id, "tidy", mine)
            self._tidying[(proj["id"], mine)] = (agent.id, now + CLAIM_SECONDS)
        fp = ForkProject(Path(proj["root"]))
        source = next((u.source for u in fp.units() if u.name == mine), None) or mine
        return TIDY.format(unit=mine, source=source, data=self._data_held(agent, proj, fp, source)), left

    def _data_held(self, agent: Agent, proj, fp: ForkProject, source: str) -> str:
        """What the globals a source file declares hold in the original (read_data's summary of each), for its tidy
        brief; '' when there's nothing to tell."""
        try:
            text = (fp.root / source).read_text(errors="replace", encoding="utf-8")
        except OSError:
            return ""
        lines = []
        for name in list(dict.fromkeys(EXTERN_DATA.findall(text)))[:DATA_SHOWN]:
            try:
                said = self.net.forks.read_data(agent, proj["slug"], name, DATA_READ)
            except Exception:  # not data the console can read (a name it doesn't know, no copy of the game)
                continue
            rows = said.splitlines()
            # a string before floats: read_data lists floats first, and text reads as floats too ("spark" did)
            told = next((r for r in rows if r.startswith("as a string:")), None) or \
                next((r for r in rows if r.startswith("as f32:")), None)
            told = told or (rows[0].split(": ", 1)[1] if rows and ": " in rows[0] else rows[1].strip() if len(rows) > 1 else "")
            if told:
                lines.append(f"   {name}: {told}")
        if not lines:
            return ""
        return "\n\nWhat the globals it declares hold in the original (read_data; check names against these):\n" + \
            "\n".join(lines)

    def tidy_done(self, agent: Agent, slug: str, unit: str, summary: str) -> str:
        """An agent's tidy of a file, done: recorded with what it changed (its edits were kept by edit_file only while
        every match held, so the file still matches)."""
        proj = self.net._project(slug)
        if not summary.strip():
            raise NetworkError("say what you changed in the summary, in a sentence or two")
        # under the lock and only while the claim lasts: a helper whose claim had run out could mark tidy a file
        # _tidy_job had given to another helper since, whose edits then landed in a file already counted tidy (#89)
        with self._tidy_lock:
            held = self._tidying.get((proj["id"], unit))
            if held is None or held[0] != agent.id or held[1] <= time.time():
                raise NetworkError(f"you aren't tidying {unit}: claim_readable gives you a file to tidy")
            del self._tidying[(proj["id"], unit)]
        with self.net.store.tx() as db:
            self.net._event(db, proj["id"], agent.id, "tidied", unit)
            self.net._event(db, proj["id"], agent.id, "tidy_summary", f"{unit}: {summary.strip()[:500]}")
        return f"✅ {unit} is tidy. claim_readable for the next one."

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
            left = None
            if fn is not None:
                # taking a held function up again is a try too: a helper whose every rewrite of one failed to compile
                # kept its claim (each compile renewed it) and got it back each session, 174 times in an afternoon on
                # an Xbox game, as only a new claim counted
                if db.execute(f"SELECT 1 FROM functions WHERE id=? AND {TODO}", (fn["id"], proj["id"])).fetchone():
                    self.net._event(db, proj["id"], agent.id, "polish", fn["name"])
                else:  # its tries are up, or it was set aside or made readable while held: the next one
                    db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
                    tried = db.execute(f"SELECT 1 WHERE ? IN ({GIVEN_UP})", (fn["name"], proj["id"])).fetchone()
                    left, fn = fn["name"] if tried else None, None
            if fn is None:
                # in ORDER. The game's own code: an Xbox game's matched SDK functions, set aside, would have been
                # polished with its 33
                fn = db.execute(f"SELECT * FROM functions WHERE project_id=? AND {TODO} "
                                f"AND id NOT IN (SELECT function_id FROM claims) ORDER BY {ORDER} LIMIT 1",
                                (proj["id"], proj["id"])).fetchone()
                if fn is not None:
                    db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + CLAIM_SECONDS))
                    self.net._event(db, proj["id"], agent.id, "polish", fn["name"])  # not "claim": the feed and routing tell them apart
        given_up = _left_as_it_is(left, "a rewrite that kept it matching") if left else ""
        if fn is None:
            if fork:  # every function done: the files next
                job, file_left = self._tidy_job(agent, proj)
                if file_left:
                    given_up += _left_as_it_is(file_left, "being tidied")
                if job is not None:
                    return given_up + job
            if self.left(proj):  # what's left is held by other helpers
                return given_up + self._held_by_others(proj)
            return (given_up + "Every matched function is readable already (or was tried "
                    f"{POLISH_TRIES} times without staying matched), and every file tidy. 🎉 Stop here: nothing is "
                    "left to polish.")
        counts = self.counts(proj)
        where = f" at 0x{fn['vram']:08X}" if fn["vram"] is not None else f" in {fn['unit']}"
        names = ("- rename_symbol the function, and the globals and functions it uses, once their purpose is clear "
                 "(read_file and search_code show what it calls and what calls it);\n"
                 "- describe structs, enums and constants in the project's headers (edit_file) and use their names here;\n"
                 if fork else
                 "- rename_symbol the function, and the globals and functions it uses, once their purpose is clear;\n"
                 "- describe structs, enums and constants in the shared header (edit_context) and use their names here;\n")
        # helpers named two getters of class names "static data" without looking at what the globals held, and each
        # picked its own style (Shape3_Init, Shape2_init, vec3_dot_product)
        names += ("- read_data a global before you name it: what it holds (a string, a table, a constant) says what it "
                  "is;\n- name things in the style the names around them already use.\n")
        inline = self.net.forks.inline_definition(ForkProject(Path(proj["root"])), fn) if fork else None
        if inline:
            names += (f"- its body is an inline in {inline[0]} (its source file only marks where the original's copy "
                      "sits): send the whole definition, still `inline`, and make_readable puts it there;\n")
        return (f"{given_up}MAKE READABLE: {fn['name']}{where}, {fn['n_insns']} instructions "
                f"({counts['readable']} of {counts['matched']} matched functions are readable so far).\n\n"
                f"It matches today with this C:\n```c\n{self._matching_c(proj, fn).strip()}\n```\n\n"
                "Make it readable without changing what the compiler makes of it:\n" + names +
                "- give parameters and locals meaningful names; write one comment saying what the function does;\n"
                "- keep the statements' shape: the order, the casts and the temporaries that make it match.\n"
                + ("- it's a big function: rename and comment in place, statement for statement; don't restructure it "
                   "(one slip anywhere in it and the rewrite is refused).\n" if fn["n_insns"] > BIG_INSNS else "") +
                f"Then make_readable(\"{fn['name']}\", source). It's judged again: it must still match byte for byte, or "
                "it's refused and the matching C stays.")

    def _held_by_others(self, proj) -> str:
        """claim's answer when everything left to polish is held by other helpers: come back later, not stop. Told to
        stop, a helper left for good, and a function or file whose holder's claim then ran out had nobody to take it
        (#89)."""
        now = time.time()
        held = [(r["name"], r["agent"], r["expires_at"]) for r in self.net.store.all(
            "SELECT f.name, a.name AS agent, c.expires_at FROM claims c "
            f"JOIN (SELECT id, name FROM functions WHERE project_id=? AND {TODO}) f ON f.id=c.function_id "
            "LEFT JOIN agents a ON a.id=c.agent_id", proj["id"], proj["id"])]
        with self._tidy_lock:
            files = [(u, who, until) for (pid, u), (who, until) in self._tidying.items()
                     if pid == proj["id"] and until > now]
        for unit, who, until in files:
            row = self.net.store.one("SELECT name FROM agents WHERE id=?", who)
            held.append((f"the file {unit}", row["name"] if row else None, until))
        held.sort(key=lambda h: h[2])
        shown = ", ".join(f"{name} ({who or '?'}, {max(0, int(until - now)) // 60} min left)"
                          for name, who, until in held[:10])
        return (f"Nothing to take right now: what's left to polish is held by other helpers: {shown}"
                f"{', ...' if len(held) > 10 else ''}. Each is free again when its helper finishes it or its claim "
                "runs out: call claim_readable again later. Don't stop.")

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
        fp = None if self.net.is_rom(proj) else ForkProject(Path(proj["root"]))
        if fp is not None and (inline := self.net.forks.inline_definition(fp, fn)) is not None:
            # a header's inline: rewritten in the header, which edit_file keeps only while every match holds (this one
            # too); into its unit's file, every rewrite defined it twice
            said = self.net.forks.edit_file(agent, proj["slug"], inline[0], inline[1].strip("\n"), source.strip("\n"),
                                            f"Polish {fn['name']}")
            if not said.startswith("✅"):
                return said
            return self._readable(proj, fn, agent, source)
        if fp is not None:  # the project's own file, judged and committed (or put back) as any compile
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
        with self.net.progress._project_lock(proj["slug"]), self.net.store.tx() as db:
            self.net.progress.check_result()
            db.execute("UPDATE functions SET matched_source=?, best_source=?, readable=1 WHERE id=?",
                       (source, source, fn["id"]))
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            self.net._event(db, proj["id"], agent.id, "readable", fn["name"])
        counts = self.counts(proj)
        return (f"✅ {fn['name']} is readable now, and still matches byte for byte ({counts['readable']} of "
                f"{counts['matched']} readable). claim_readable for the next one.")
