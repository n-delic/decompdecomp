"""The coordination core under the app's engine (app/engine): who works on what, what counts as a match, and what
everyone has learned.

Every public method here is one agent-facing action. They return plain text because the consumer is an LLM reading
a tool result.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import agents, scoring
from .compiler import compile_source
from .elfobj import FunctionObj, Reloc, extract_function
from .project import compiler_config, compiler_for
from .store import Store

CLAIM_TTL = 30 * 60
# A function an agent just released goes to the back of the queue for this long, for every agent: the next one
# starts on something fresh instead of passing the same function back and forth. Asked for by name, it comes at once.
# For agents of the released one's model family it stays at the back until the shared header or the compiler changes
# (see model_family): only 5.5% of released functions ever matched later, and another try by the same model mostly
# fails the same way.
RELEASE_COOLDOWN = 15 * 60
# The family of a helper's model, from its name (app/agents.py helper_name: kind-model-n): its agent's, when the
# agent runs one maker's models (agents.FAMILIES: Claude Code Anthropic's, Codex OpenAI's, Antigravity Google's),
# else the model the name says (opencode and API helpers).
MODEL_FAMILIES = {"opus": "claude", "sonnet": "claude", "haiku": "claude", "fable": "claude", "o3": "gpt", "o4": "gpt"}
# When compile_and_diff tells an agent to move on: after this many compiles in a row that didn't beat its best score,
# by the function's size (the first number of instructions it's under). Functions that match mostly do in the first
# few compiles (up to 20 instructions: 43%, 29%, 17%, 11% of the first four compiles matched, October 2026), so small
# ones get few; big ones get more, as long as the score keeps falling.
STALL_BUDGET = ((32, 3), (64, 4), (128, 6), (256, 8))
STALL_BUDGET_BIGGEST = 10
STALL_LOOKBACK = 16  # compiles stall_note reads back: more than the biggest budget
BRIEF_NOTES = 8  # the newest notes on a function its brief shows
NOTE_CHARS = 4000  # a note longer than this is cut: notes are for findings, not code dumps
NOTES_FOUND = 20  # search_notes' answer at most
MACHINE_AGENTS = ("autopilot", "permuter")  # decompdecomp's own workers: their claims never mean "given up on"
# Routing between models (claim_row): on an N64 game free models matched 14-25% of the functions they claimed and
# spent 17-31 compiles a match, stronger ones 42-77% and 3-6, and every model was handed the functions the others
# gave up on. A model well behind the strongest one at work leaves functions given up on ESCALATE_AFTER times to it;
# the strongest takes those first. A model's share: its matches over its claims in the project, pulled towards the
# game's overall share while it has made few.
ESCALATE_AFTER = 3
WEAK_SHARE = 0.6  # of the strongest model's share: below it, a model is the weaker one
ROUTE_PRIOR = 10  # claims' worth of the overall share
ROUTE_ACTIVE_SECONDS = 3600  # the models at work: claimed something this recently
ROUTE_MIN_CLAIMS = 20  # claims a model has made in the project before it's judged
# Twins first (see app/twins.py): a function whose code the game has more than once (boost: how many more times)
# and isn't too big or given up on, best value per instruction first; its twins (boost -1) last, as they match for
# free once it does.
TWIN_MAX_INSNS = 400  # a bigger function waits its turn by size, twins or not: one match of it takes long
TWIN_MAX_ABANDONS = 2  # agents gave it back this often: it's hard, twins or not
# value per instruction: the copies a match brings, over the size; SQL divides integers, so it's scaled up, and a few
# instructions are added so a two-instruction twin doesn't beat a real function with many copies
TWIN_VALUE_SCALE, TWIN_SIZE_FLOOR = 64, 16
_TWINS = (f"(f.boost > 0 AND f.n_insns <= {TWIN_MAX_INSNS} AND f.abandons < {TWIN_MAX_ABANDONS}) DESC, "
          f"CASE WHEN f.boost > 0 THEN (f.boost + 1) * {TWIN_VALUE_SCALE} / (f.n_insns + {TWIN_SIZE_FLOOR}) ELSE 0 "
          f"END DESC, f.boost < 0, ")
# Where in the game a function is says much about its chances: on GTA San Andreas small functions matched 19-28% of
# the time in most of the game and 87% in one part of it; on Mario Kart 64 one part 19%, the rest 71-85% (October
# 2026). Mostly another compiler or flags built the bad parts. So among functions of about the same size, the one
# from the part where matches happen comes first (its share of tried functions matched, pulled towards the game's
# overall share while little there is tried), and a part that keeps failing comes after everything else: more
# agents won't fix a wrong compiler, the compiler map and range checks will.
REGION_BYTES = 0x40000  # one part of the game: 256 KB of code
REGION_PRIOR = 8  # tries' worth of the game's overall share, so a part judged on two functions isn't judged yet
FAILING_SHARE, FAILING_TRIED = 0.15, 20
SIZE_STEP = 64  # instructions: functions within one step are "about the same size"
FINISH_SIZE_STEP = 32  # the same for "finish", in finer steps: there the file closest to done decides within one
_REGION = (f"(SELECT vram / {REGION_BYTES} AS region, SUM(status = 'matched') AS won, "
           f"SUM(status = 'matched' OR attempts > 0) AS tried FROM functions WHERE project_id = :pid "
           f"AND status != 'asm' AND vram IS NOT NULL GROUP BY region)")
_SHARE = f"((COALESCE(r.won, 0) + {REGION_PRIOR} * :overall) / (COALESCE(r.tried, 0) + {REGION_PRIOR}))"
_FAILING = f"(COALESCE(r.tried, 0) >= {FAILING_TRIED} AND {_SHARE} < {FAILING_SHARE})"
STRATEGIES = {
    # (a failing part waits even for its twins: they'd fail the same way)
    "easiest": f"{_FAILING}, " + _TWINS + f"f.abandons > 0, (f.n_insns + {SIZE_STEP - 1}) / {SIZE_STEP} ASC, "
                                          f"{_SHARE} DESC, f.n_insns ASC",
    # what nobody gave up on, small first; among functions of about the same size, the files closest to done (a
    # file is linked into the game once all of it matches). Leftovers agents released again and again come last,
    # or a crowd of agents spends its time passing the hardest functions around.
    "finish": f"{_FAILING}, " + _TWINS + f"f.abandons ASC, (f.n_insns + {FINISH_SIZE_STEP - 1}) / {FINISH_SIZE_STEP} ASC, "
                       "(SELECT COUNT(*) FROM functions g WHERE "
                       "g.project_id=f.project_id AND g.unit=f.unit AND g.status='open') ASC, f.n_insns ASC",
    # near-misses, the ones given up on least first (else every agent resumes the same stuck one)
    "closest": "f.best_score IS NULL, f.abandons ASC, CAST(f.best_score AS REAL) / (f.n_insns * 100) ASC",
    "hardest": "f.n_insns DESC",
    "random": "RANDOM()",
}


class NetworkError(Exception):
    """An error meant to be shown to the agent as-is."""


@dataclass
class Agent:
    id: int
    name: str
    owner: str | None
    trusted: bool = False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def model_family(agent_name: str) -> str:
    """Who made an agent's model ("claude-opus-1": claude, "opencode-deepseek-v4.1-flash-2": deepseek); an agent
    whose name says no model is a family of its own."""
    kind, _, rest = agent_name.lower().partition("-")
    if kind in agents.FAMILIES:
        return agents.FAMILIES[kind]
    word = re.match(r"[a-z]+\d*", rest)
    if word is None:
        return kind
    return MODEL_FAMILIES.get(word.group(0), re.sub(r"\d+$", "", word.group(0)))


def stall_budget(n_insns: int) -> int:
    """Compiles in a row without a better score before an agent is told to move on (STALL_BUDGET)."""
    return next((after for under, after in STALL_BUDGET if n_insns < under), STALL_BUDGET_BIGGEST)


def stall_budget_text() -> str:
    """The budget in words: the prompt and the workflow guide say exactly this."""
    return (f"no better score after {STALL_BUDGET[0][1]} compiles in a row for small functions, up to "
            f"{STALL_BUDGET_BIGGEST} for the biggest")


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


class Network:
    def __init__(self, db_path: str | Path, claim_ttl: int = CLAIM_TTL):
        self.store = Store(db_path)
        self.claim_ttl = claim_ttl
        self._pool = ThreadPoolExecutor(max_workers=8)

    # ------------------------------------------------------------------ agents

    def register_agent(self, name: str, owner: str | None = None, *, trusted: bool = True, origin: str = "cli") -> str:
        """Mint an agent token for one of the app's helpers (`trusted` stays on the record for older databases)."""
        name = name.strip()
        if not name or len(name) > 40 or not all(c.isalnum() or c in "-_." for c in name):
            raise NetworkError("agent name must be 1-40 chars of [A-Za-z0-9-_.]")
        owner = (owner or "").strip()[:60] or None
        token = "dn_" + secrets.token_urlsafe(24)
        now = time.time()
        with self.store.tx() as db:
            if db.execute("SELECT 1 FROM agents WHERE lower(name)=lower(?)", (name,)).fetchone():
                raise NetworkError(f"agent name {name!r} is taken")
            db.execute(
                "INSERT INTO agents(name,owner,token_hash,created_at,trusted,origin,terms_accepted_at) VALUES(?,?,?,?,?,?,?)",
                (name, owner, hash_token(token), now, int(trusted), origin, None),
            )
        return token

    def authenticate(self, token: str | None) -> Agent:
        """The agent a token belongs to (helpers get theirs from the app when it starts them)."""
        if not token:
            raise NetworkError("missing token: only helpers decompdecomp starts can work on its projects")
        row = self.store.one("SELECT id,name,owner,trusted FROM agents WHERE token_hash=?", hash_token(token))
        if row is None:
            raise NetworkError("invalid API token")
        with self.store.tx() as db:
            db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))

    # ---------------------------------------------------------------- helpers

    def _project(self, slug: str | None):
        if slug:
            row = self.store.one("SELECT * FROM projects WHERE slug=?", slug)
            if row is None:
                raise NetworkError(f"no project {slug!r} here: check its name, or whether it was removed")
            return row
        rows = self.store.all("SELECT * FROM projects")
        if len(rows) == 1:
            return rows[0]
        raise NetworkError("several projects exist; pass `project` (its slug)")

    def _function(self, name: str, project: str | None):
        if project is None and "/" in name and "@" not in name.split("/", 1)[0]:  # project/name (fork names hold units)
            project, name = name.split("/", 1)
        if project is None:
            rows = self.store.all("SELECT * FROM functions WHERE name=?", name)
            if len(rows) > 1:
                raise NetworkError(f"{name!r} exists in several projects; pass `project`")
            row = rows[0] if rows else None
        else:
            row = self.store.one(
                "SELECT f.* FROM functions f JOIN projects p ON p.id=f.project_id WHERE p.slug=? AND f.name=?",
                project, name,
            )
        if row is None:
            raise NetworkError(f"no function {name!r}" + (f" in {project!r}" if project else ""))
        return row

    def _context(self, project_id: int, unit: str | None = ""):
        """Latest header for a translation unit, falling back to the project-wide one. Real codebases have
        one header set per source file; small projects just have the project-wide header ('' unit)."""
        unit = unit or ""
        return self.store.one(
            "SELECT * FROM context_versions WHERE project_id=? AND unit IN (?, '') "
            "ORDER BY unit=? DESC, version DESC LIMIT 1", project_id, unit, unit)

    def _unit_contexts(self, project_id: int) -> set[str]:
        return {r["unit"] for r in self.store.all(
            "SELECT DISTINCT unit FROM context_versions WHERE project_id=? AND unit != ''", project_id)}

    def _effective_unit(self, fn, units: set[str]) -> str:
        return fn["unit"] if fn["unit"] in units else ""

    def _target_obj(self, fn, proj) -> FunctionObj:
        return FunctionObj(
            name=fn["name"], arch=proj["arch"], big_endian=bool(proj["big_endian"]), code=fn["target"],
            relocs=[Reloc.from_json(r) for r in json.loads(fn["relocs"])],
        )

    def _expire_claims(self, db) -> None:
        now = time.time()
        own = {r["id"] for r in db.execute("SELECT id FROM agents WHERE name IN (?, ?)", MACHINE_AGENTS)}
        for row in db.execute("SELECT c.function_id, c.agent_id, f.project_id, f.name FROM claims c "
                              "JOIN functions f ON f.id=c.function_id WHERE c.expires_at < ?", (now,)).fetchall():
            if row["agent_id"] in own:  # decompdecomp's own tries (cut short by a restart): nobody gave up on it
                continue
            db.execute("UPDATE functions SET abandons=abandons+1 WHERE id=?", (row["function_id"],))
            self._event(db, row["project_id"], row["agent_id"], "expired", row["name"])
        db.execute("DELETE FROM claims WHERE expires_at < ?", (now,))

    def _event(self, db, project_id, agent_id, kind, detail) -> None:
        db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                   (project_id, agent_id, kind, detail, time.time()))

    def _agent_name(self, agent_id) -> str:
        row = self.store.one("SELECT name FROM agents WHERE id=?", agent_id) if agent_id else None
        return row["name"] if row else "?"

    def _compile(self, proj, header: str, source: str, fn=None):
        """Compiled with the project's compiler, or for `fn` the one its address range is built with."""
        cfg = json.loads(proj["config"])
        if fn is not None and cfg.get("compiler_ranges"):
            cfg = {**cfg, "compiler": compiler_for(cfg, fn["vram"])}
        return compile_source(compiler_config(Path(proj["root"]), cfg), header, source)

    def _evaluate(self, fn, proj, obj: bytes) -> tuple[FunctionObj | None, bool | None, list[str]]:
        """(candidate, verdict, problems) for a compiled object. verdict None means: decide by comparing
        fingerprints. Projects whose targets come from a ROM link the candidate instead (see app.engine)."""
        return extract_function(obj, fn["name"]), None, []

    def _asm_text(self, fn, proj) -> str:
        return fn["asm"]

    def _extra_brief(self, fn, proj) -> list[str]:
        return []

    # ---------------------------------------------------------------- projects

    def get_context(self, project: str | None, function: str | None = None) -> str:
        """The shared header a function compiles with, as agents read it (a project with one header per source
        file needs the function)."""
        if function:
            fn = self._function(function, project)
            proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
            ctx = self._context(proj["id"], fn["unit"])
        else:
            proj = self._project(project)
            if self._unit_contexts(proj["id"]):
                raise NetworkError(f"{proj['slug']} has one header per source file: pass `function` to get the "
                                   f"header that function compiles against")
            ctx = self._context(proj["id"])
        return (f"// context.h for {proj['slug']} — version {ctx['version']} "
                f"(prepended to every compile as #include \"context.h\")\n{ctx['header']}")

    # ------------------------------------------------------------------ claims

    def claim(self, agent: Agent, project: str | None = None, function: str | None = None,
              strategy: str = "easiest") -> str:
        fn = self.claim_row(agent, project, function, strategy)
        if fn is None:
            proj = self._project(project)
            return f"Nothing left to claim in {proj['slug']} — every open function is matched or claimed. 🎉"
        return self._brief(fn, agent, header="CLAIMED")

    def claim_row(self, agent: Agent, project: str | None = None, function: str | None = None,
                  strategy: str = "easiest"):
        """Claim (or renew) a function and return its row, or None when nothing is left."""
        if strategy not in STRATEGIES:
            raise NetworkError(f"strategy must be one of {', '.join(STRATEGIES)}")
        proj = self._project(project) if (project or function is None) else None
        now = time.time()
        with self.store.tx() as db:
            self._expire_claims(db)
            held = db.execute("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                              (agent.id,)).fetchone()
            if held is not None:
                if function and held["id"] != self._function(function, proj["slug"] if proj else None)["id"]:
                    raise NetworkError(f"you already hold {held['name']}; match it or release_function it first")
                fn = held
                db.execute("UPDATE claims SET expires_at=? WHERE function_id=?", (now + self.claim_ttl, fn["id"]))
            elif function:
                fn = self._function(function, proj["slug"] if proj else None)
                if fn["status"] == "matched":
                    raise NetworkError(f"{fn['name']} is already matched; use get_function to read its source")
                if fn["status"] == "asm":
                    raise NetworkError(f"{fn['name']} is hand-written assembly: no C compiles to it, so it's left out")
                other = db.execute("SELECT agent_id, expires_at FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
                if other is not None:
                    raise NetworkError(f"{fn['name']} is claimed by {self._agent_name(other['agent_id'])} "
                                       f"for another {int(other['expires_at'] - now) // 60} min")
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
            else:
                route = self._route(db, proj["id"], agent, now)
                family = model_family(agent.name)
                kin = [r["id"] for r in db.execute("SELECT id, name FROM agents") if model_family(r["name"]) == family]
                tried = db.execute("SELECT SUM(status = 'matched') AS won, SUM(status = 'matched' OR attempts > 0) "
                                   "AS tried FROM functions WHERE project_id=? AND status != 'asm'", (proj["id"],)).fetchone()
                overall = (tried["won"] or 0) / tried["tried"] if tried["tried"] else 0.5
                query = (
                    f"""SELECT f.* FROM functions f LEFT JOIN {_REGION} r ON r.region = f.vram / {REGION_BYTES}
                        WHERE f.project_id=:pid AND f.status='open' AND (:weak = 0 OR f.abandons < :escalate)
                        AND f.id NOT IN (SELECT function_id FROM claims)
                        AND f.name NOT IN (SELECT detail FROM events WHERE project_id=:pid AND agent_id=:agent
                                           AND kind='release')
                        ORDER BY f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                            AND agent_id IN (SELECT value FROM json_each(:kin)) AND created_at >
                                            COALESCE((SELECT MAX(created_at) FROM events WHERE project_id=:pid
                                                      AND kind IN ('context', 'compiler')), 0)),
                                 f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                            AND created_at > :cooled), :strong AND f.abandons >= :escalate DESC,
                                 {STRATEGIES[strategy]} LIMIT 1""")
                args = {"pid": proj["id"], "agent": agent.id, "kin": json.dumps(kin), "cooled": now - RELEASE_COOLDOWN,
                        "overall": overall, "escalate": ESCALATE_AFTER, "weak": route == "weak", "strong": route == "strong"}
                fn = db.execute(query, args).fetchone()
                if fn is None and route == "weak":  # only what stronger models are for is left: better tried than idle
                    fn = db.execute(query, args | {"weak": False}).fetchone()
                if fn is None:
                    return None
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
        return fn

    def _route(self, db, project_id: int, agent: Agent, now: float) -> str | None:
        """"weak" for a model at work in the project well behind the strongest one at work, "strong" for that one
        while a weak one works too (see ESCALATE_AFTER), None otherwise: no routing until both are told apart by
        ROUTE_MIN_CLAIMS claims each."""
        def model(name: str) -> str:
            return re.sub(r"-\d+$", "", name)  # an agent's model: its name less its number (claude-sonnet-5-5-1)

        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM agents")}
        claims, won, busy = collections.Counter(), collections.Counter(), {model(agent.name)}
        for r in db.execute("SELECT agent_id, COUNT(*) AS n, MAX(created_at) AS last FROM events WHERE project_id=? "
                            "AND kind='claim' GROUP BY agent_id", (project_id,)):
            if names.get(r["agent_id"], MACHINE_AGENTS[0]) not in MACHINE_AGENTS:
                claims[model(names[r["agent_id"]])] += r["n"]
                if r["last"] > now - ROUTE_ACTIVE_SECONDS:
                    busy.add(model(names[r["agent_id"]]))
        for r in db.execute("SELECT matched_agent_id AS id, COUNT(*) AS n FROM functions WHERE project_id=? "
                            "AND status='matched' GROUP BY matched_agent_id", (project_id,)):
            if names.get(r["id"], MACHINE_AGENTS[0]) not in MACHINE_AGENTS:
                won[model(names[r["id"]])] += r["n"]
        overall = sum(won.values()) / sum(claims.values()) if sum(claims.values()) else 0.5
        share = {m: (won[m] + ROUTE_PRIOR * overall) / (claims[m] + ROUTE_PRIOR) for m in busy
                 if claims[m] >= ROUTE_MIN_CLAIMS}
        mine = model(agent.name)
        if mine not in share or len(share) < 2:
            return None
        best = max(share.values())
        if share[mine] < WEAK_SHARE * best:
            return "weak"
        weak = any(s < WEAK_SHARE * best for s in share.values())
        return "strong" if share[mine] == best and weak else None

    def release(self, agent: Agent, function: str, notes: str, project: str | None = None) -> str:
        """An agent gives a function back: its claim ends, its notes stay for the next agent, and the function
        counts one more give-up (the claim order puts those later)."""
        fn = self._function(function, project)
        with self.store.tx() as db:
            row = db.execute("SELECT agent_id FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
            if row is None or row["agent_id"] != agent.id:
                raise NetworkError(f"you don't hold a claim on {fn['name']}")
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            db.execute("UPDATE functions SET abandons=abandons+1 WHERE id=?", (fn["id"],))
            if notes.strip():
                db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)",
                           (fn["project_id"], fn["id"], agent.id, notes.strip(), time.time()))
            self._event(db, fn["project_id"], agent.id, "release", fn["name"])
        return (f"Released {fn['name']}. Your best attempt and notes are saved so the next agent starts "
                f"where you left off. Call claim_function for a new one.")

    def get_function(self, function: str, project: str | None = None, agent: Agent | None = None) -> str:
        return self._brief(self._function(function, project), agent, header="VIEW")

    def _brief(self, fn, agent: Agent | None, header: str) -> str:
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        ctx = self._context(proj["id"], fn["unit"])
        claim = self.store.one("SELECT * FROM claims WHERE function_id=?", fn["id"])
        lines = [f"## {header}: {proj['slug']}/{fn['name']}",
                 f"size {fn['size']} bytes, {fn['n_insns']} instructions, arch {proj['arch']}, "
                 f"context.h v{ctx['version']} ({len(ctx['header'].splitlines())} lines)"]
        cfg = json.loads(proj["config"])
        own = compiler_for(cfg, fn["vram"])
        if own is not cfg.get("compiler"):  # a range built by another compiler than the rest of the game
            lines.append(f"compiled with {own['toolchain']} {' '.join(own['flags'])}: this address range's compiler, "
                         f"not the project's")
        if claim:
            who = "you" if agent and claim["agent_id"] == agent.id else self._agent_name(claim["agent_id"])
            lines.append(f"claimed by {who}, lease expires in {int(claim['expires_at'] - time.time()) // 60} min "
                         f"(each compile_and_diff renews it)")
        if fn["status"] == "matched":
            lines += [f"MATCHED by {self._agent_name(fn['matched_agent_id'])} {_ago(fn['matched_at'])}:",
                      "```c", fn["matched_source"].strip(), "```"]
        lines += ["", "### Target assembly", "```asm", scoring.compact_asm(self._asm_text(fn, proj)), "```"]
        lines += self._extra_brief(fn, proj)
        if fn["status"] != "matched":
            if fn["best_source"]:
                lines += ["", f"### Best attempt so far: score {fn['best_score']} by "
                              f"{self._agent_name(fn['best_agent_id'])} ({fn['attempts']} attempts total) — "
                              f"start from this", "```c", fn["best_source"].strip(), "```"]
            elif fn["seed"]:
                lines += ["", "### Machine draft (m2c): its control flow and arithmetic are usually right, its types "
                              "and names usually wrong. Start from it and fix those", "```c", fn["seed"].strip(), "```"]
        notes = self.store.all(
            "SELECT n.text, n.created_at, a.name FROM notes n LEFT JOIN agents a ON a.id=n.agent_id "
            f"WHERE n.function_id=? ORDER BY n.id DESC LIMIT {BRIEF_NOTES}", fn["id"])
        if notes:
            lines += ["", "### Notes from previous agents on this function"]
            lines += [f"- ({n['name']}, {_ago(n['created_at'])}) {n['text']}" for n in notes]
        if fn["unit"] and fn["unit"] in self._unit_contexts(proj["id"]):
            lines.insert(2, f"source unit {fn['unit']}: call get_context with function=\"{fn['name']}\" for its header")
        if header == "CLAIMED":
            lines += ["", self._next_step(proj, ctx)]
        return "\n".join(lines)

    def _next_step(self, proj, ctx) -> str:
        """The brief's last line for the agent that claimed the function."""
        return ("Next: read context.h with get_context, write C for this function, and iterate with "
                "compile_and_diff until score is 0. Submit only the function body (plus any helper "
                "declarations); context.h is included automatically.")

    # --------------------------------------------------------- compile & diff

    def compile_and_diff(self, agent: Agent, function: str, source: str, project: str | None = None) -> str:
        return self.attempt(agent, function, source, project)["text"]

    def attempt(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        """Compile, score and record one attempt. Returns {"text", "score", "matched"} (score None: didn't compile)."""
        fn = self._function(function, project)
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        now = time.time()

        # Claim bookkeeping first: you can't score (or get credit) on someone else's function.
        with self.store.tx() as db:
            self._expire_claims(db)
            claim = db.execute("SELECT agent_id FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
            scratch = fn["status"] == "matched"
            if not scratch:
                if claim is None:
                    held = db.execute("SELECT 1 FROM claims WHERE agent_id=?", (agent.id,)).fetchone()
                    if held:
                        raise NetworkError("you hold another claim; release it before working on a different function")
                    db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                    self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
                elif claim["agent_id"] != agent.id:
                    raise NetworkError(f"{fn['name']} is claimed by {self._agent_name(claim['agent_id'])}")
                else:
                    db.execute("UPDATE claims SET expires_at=? WHERE function_id=?", (now + self.claim_ttl, fn["id"]))

        ctx = self._context(proj["id"], fn["unit"])
        result = self._compile(proj, ctx["header"], source, fn)

        if not result.ok:
            self._record(fn, agent, None, source, ctx["version"])
            return {"text": f"❌ COMPILE ERROR (context v{ctx['version']})\n{result.log.strip()}", "score": None, "matched": False}

        cand, verdict, problems = self._evaluate(fn, proj, result.obj)
        if cand is None:
            self._record(fn, agent, None, source, ctx["version"])
            return {"text": f"❌ Compiled, but the object has no function named `{fn['name']}`. "
                            f"Define it with exactly that name (non-static).", "score": None, "matched": False}
        d = scoring.diff(self._target_obj(fn, proj), cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        matched_now = d.bytes_match and not scratch
        last = self.store.one("SELECT score FROM attempts WHERE function_id=? AND agent_id=? AND score IS NOT NULL "
                              "ORDER BY id DESC LIMIT 1", fn["id"], agent.id)
        self._record(fn, agent, d.score, source, ctx["version"], matched=matched_now)

        if scratch:
            head = f"(scratch — {fn['name']} was already matched) score {d.score}"
        elif d.bytes_match:
            return {"text": f"✅ MATCH! {fn['name']} is byte-identical to the target ({fn['n_insns']} instructions). "
                            f"Credited to {agent.name}; your claim is released. Call claim_function for the next one.",
                    "score": 0, "matched": True}
        else:
            best = self.store.one("SELECT best_score FROM functions WHERE id=?", fn["id"])["best_score"]
            head = (f"score {d.score} ({scoring.percent_text(d.percent)} match) — best so far {best}. "
                    f"Target {len([r for r in d.rows if r[1]])} insns, yours {len([r for r in d.rows if r[2]])}.")
            head += self._what_changed(d, last["score"] if last else None)
            if d.rows and all(m == " " for m, _, _ in d.rows):
                head += ("\nNOT A MATCH YET: every instruction reads the same, but the bytes differ"
                         + (" because of the linking notes at the end: fix those." if problems else
                            ": an encoding the listing doesn't show (branch form, an immediate, a relocation)."))
        warn = f"\ncompiler output:\n{result.log.strip()}" if result.log.strip() else ""
        if problems:
            warn += "\nwhen linking your code at the function's address:\n" + "\n".join(f"- {p}" for p in problems)
        return {"text": f"{head}\n{d.render()}{warn}{self.stall_note(fn['id'], agent.id)}", "score": d.score, "matched": False}

    @staticmethod
    def _what_changed(d: scoring.DiffResult, last: int | None) -> str:
        """What the agent's last change did, and where the first difference is now: one small change per compile
        works best, and an agent that sees which way the score moved knows whether to keep or undo it."""
        said = ""
        if last is not None:
            said = ("\nYour last change: no effect on the score." if d.score == last else
                    f"\nYour last change made it better ({last} → {d.score}): keep it." if d.score < last else
                    f"\nYour last change made it worse ({last} → {d.score}): undo it, or it fixed one thing and broke another.")
        same, total, row = scoring.first_mismatch(d.rows)
        if row is not None:
            mark, target, yours = row
            said += (f"\nThe first {same} of {total} target instructions match; the first difference "
                     f"({scoring.MARKERS.get(mark, mark)}): target `{target or '-'}`, yours `{yours or '-'}`.")
        return said

    def stall_note(self, function_id: int, agent_id: int, after: int | None = None) -> str:
        """A nudge once an agent's last `after` compiles of a function didn't beat its best: functions that match
        mostly do in the first few tries, and the agent's time is better spent on the next one. Big functions get
        longer (a quarter of Mario Party 6's agent matches over 64 instructions took more than 4 compiles, and
        those functions hold most of the code left): STALL_BUDGET."""
        if after is None:
            row = self.store.one("SELECT n_insns FROM functions WHERE id=?", function_id)
            after = stall_budget((row["n_insns"] if row else 0) or 0)
        rows = self.store.all(f"SELECT score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT {STALL_LOOKBACK}",
                              function_id, agent_id)
        best, since = None, 0
        for r in reversed(rows):
            if r["score"] is not None and (best is None or r["score"] < best):
                best, since = r["score"], 0
            else:
                since += 1
        if since < after:
            return ""
        return (f"\n\n⏭ No better in your last {since} compiles. release_function with notes (what you tried, what's "
                "left) and claim the next one: functions that match mostly do in the first few tries, and this one "
                "keeps your best attempt for whoever comes next.")

    def _record(self, fn, agent: Agent, score: int | None, source: str, ctx_version: int, matched: bool = False,
                compiled: bool | None = None, via: str = "agent"):
        now = time.time()
        compiled = score is not None if compiled is None else compiled
        with self.store.tx() as db:
            db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at,via) "
                       "VALUES(?,?,?,?,?,?,?,?)", (fn["id"], agent.id, score, compiled, ctx_version, source, now, via))
            db.execute("UPDATE functions SET attempts=attempts+1 WHERE id=?", (fn["id"],))
            if score is not None:
                db.execute("UPDATE functions SET best_score=?, best_source=?, best_agent_id=? "
                           "WHERE id=? AND status='open' AND (best_score IS NULL OR best_score > ?)",
                           (score, source, agent.id, fn["id"], score))
            if matched:
                db.execute("UPDATE functions SET status='matched', matched_source=?, matched_agent_id=?, "
                           "matched_at=?, best_score=0, best_source=? WHERE id=?",
                           (source, agent.id, now, source, fn["id"]))
                db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
                self._event(db, fn["project_id"], agent.id, "match", fn["name"])
        if matched:
            self.on_match(self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn["id"], agent)

    def on_match(self, proj, function_id: int, agent: Agent | None = None) -> None:
        """A function just matched (subclasses try its code on its twins)."""

    # -------------------------------------------------------- shared context

    def edit_context(self, agent: Agent, project: str | None, old: str, new: str, message: str,
                     function: str | None = None) -> str:
        """Exact-string edit of a header. Accepted only if every already-matched function compiled against
        that header still matches with the new one — that's the project's test suite."""
        if function:
            fn = self._function(function, project)
            proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
            unit = self._effective_unit(fn, self._unit_contexts(proj["id"]))
        else:
            proj = self._project(project)
            if self._unit_contexts(proj["id"]):
                raise NetworkError(f"{proj['slug']} has one header per source file: pass `function` to edit the "
                                   f"header that function compiles against")
            unit = ""
        if not message.strip():
            raise NetworkError("give a short commit message explaining the change")
        for _ in range(3):
            ctx = self._context(proj["id"], unit)
            header = ctx["header"]
            if old:
                from .textedit import replace_once

                new_header = replace_once(header, old, new, "the header")
            else:
                new_header = header.rstrip("\n") + "\n\n" + new.strip("\n") + "\n"
            members = self._matched_using(proj, unit)
            broken = self.regression_test(proj, new_header, only=members)
            if broken:
                listing = "\n".join(f"- {name}: {why}" for name, why in broken[:10])
                return (f"❌ REJECTED: the edit breaks {len(broken)} already-matched function(s), so the header "
                        f"stays at v{ctx['version']}.\n{listing}")
            with self.store.tx() as db:
                if self._latest_version(db, proj["id"], unit) != ctx["version"]:
                    continue  # someone else committed meanwhile: re-apply on top and re-test
                version = self._next_version(db, proj["id"])
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, new_header, agent.id, message.strip(), time.time(), unit))
                self._event(db, proj["id"], agent.id, "context", f"v{version}{f' ({unit})' if unit else ''}: {message.strip()}")
            return f"✅ header is now v{version} (all {len(members)} matched functions that use it still match)."
        raise NetworkError("the header is being edited heavily right now; re-read it and retry")

    def rename_symbol(self, agent: Agent, project: str | None, old: str, new: str, message: str = "") -> str:
        """Project-wide refactor: rename an identifier (field, type, global, function param...) in every
        header *and* in every matched source / best attempt, then regression-test the result.
        The rename is textual (whole words), so it is meant for unique names like `unk_1C` or `func_80012345`."""
        proj = self._project(project)
        ident = re.compile(r"^[A-Za-z_]\w*$")
        if not (ident.match(old) and ident.match(new)):
            raise NetworkError("old and new must be C identifiers")
        word = re.compile(rf"\b{re.escape(old)}\b")
        for _ in range(3):
            units = self._unit_contexts(proj["id"])
            current = {u: self._context(proj["id"], u) for u in ({""} | units)}
            changed = {u: word.sub(new, c["header"]) for u, c in current.items() if word.search(c["header"])}
            if not changed:
                raise NetworkError(f"`{old}` does not occur in any header")
            matched = self.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
            rewritten = {r["id"]: word.sub(new, r["matched_source"]) for r in matched}
            header_for = lambda fn: changed.get(self._effective_unit(fn, units), current[self._effective_unit(fn, units)]["header"])  # noqa: B023  called within this pass of the loop
            broken = self.regression_test(proj, None, sources=rewritten, only=matched, header_for=header_for)
            if broken:
                listing = "\n".join(f"- {name}: {why}" for name, why in broken[:10])
                return f"❌ REJECTED: renaming breaks {len(broken)} matched function(s).\n{listing}"
            msg = message.strip() or f"rename {old} -> {new}"
            with self.store.tx() as db:
                n_matched = db.execute("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='matched'",
                                       (proj["id"],)).fetchone()["n"]
                if n_matched != len(matched) or any(self._latest_version(db, proj["id"], u) != current[u]["version"]
                                                    for u in changed):
                    continue
                for u, header in changed.items():
                    version = self._next_version(db, proj["id"])
                    db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                               "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, header, agent.id, msg, time.time(), u))
                for fid, src in rewritten.items():
                    db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (src, src, fid))
                for r in db.execute("SELECT id, best_source FROM functions WHERE project_id=? AND status='open' "
                                    "AND best_source IS NOT NULL", (proj["id"],)).fetchall():
                    db.execute("UPDATE functions SET best_source=? WHERE id=?", (word.sub(new, r["best_source"]), r["id"]))
                self._event(db, proj["id"], agent.id, "context", f"{msg} ({len(changed)} header(s))")
            return (f"✅ renamed `{old}` -> `{new}` in {len(changed)} header(s) and in {len(rewritten)} matched "
                    f"sources; all still match.")
        raise NetworkError("headers are being edited heavily right now; retry")

    def _latest_version(self, db, project_id: int, unit: str) -> int | None:
        row = db.execute("SELECT MAX(version) AS v FROM context_versions WHERE project_id=? AND unit=?",
                         (project_id, unit)).fetchone()
        return row["v"]

    def _next_version(self, db, project_id: int) -> int:
        return (db.execute("SELECT MAX(version) AS v FROM context_versions WHERE project_id=?",
                           (project_id,)).fetchone()["v"] or 0) + 1

    def _matched_using(self, proj, unit: str) -> list:
        units = self._unit_contexts(proj["id"])
        return [f for f in self.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
                if self._effective_unit(f, units) == unit]

    def regression_test(self, proj, header: str | None, sources: dict[int, str] | None = None,
                        only: list | None = None, header_for=None) -> list[tuple[str, str]]:
        """Every matched function (or `only`) built again with a changed header or sources: (name, why) of each
        that no longer matches, so a header edit that breaks matches is refused."""
        matched = only if only is not None else self.store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])

        def check(fn) -> tuple[str, str] | None:
            res = self._compile(proj, header_for(fn) if header_for else header,
                                (sources or {}).get(fn["id"], fn["matched_source"]), fn)
            if not res.ok:
                return fn["name"], "no longer compiles: " + res.log.strip().splitlines()[0][:200] if res.log.strip() else "compile failed"
            cand, verdict, _ = self._evaluate(fn, proj, res.obj)
            if verdict is None and cand is not None:
                want = self._target_obj(fn, proj).fingerprint()
                verdict = cand.fingerprint() == want
            if cand is None or not verdict:
                return fn["name"], "compiles but no longer matches"
            return None

        if not matched:  # nothing to protect, but the header must at least parse
            if header is None:
                return []
            res = self._compile(proj, header, "")
            return [] if res.ok else [("<header>", res.log.strip()[:300])]
        return [b for b in self._pool.map(check, matched) if b]

    # ------------------------------------------------------------------ notes

    def add_note(self, agent: Agent, project: str | None, text: str, function: str | None = None) -> str:
        """A note for the agents after this one, on a function or on the whole project."""
        proj = self._project(project)
        fn = self._function(function, proj["slug"]) if function else None
        if not text.strip():
            raise NetworkError("empty note")
        with self.store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)",
                       (proj["id"], fn["id"] if fn else None, agent.id, text.strip()[:NOTE_CHARS], time.time()))
        return "Noted. Other agents will see it" + (f" when they open {fn['name']}." if fn else " in their briefings.")

    def search_notes(self, project: str | None, query: str) -> str:
        """Notes on the project that contain `query`, newest first."""
        proj = self._project(project)
        rows = self.store.all(
            "SELECT n.text, n.created_at, a.name AS agent, f.name AS fn FROM notes n "
            "LEFT JOIN agents a ON a.id=n.agent_id LEFT JOIN functions f ON f.id=n.function_id "
            f"WHERE n.project_id=? AND n.text LIKE ? ORDER BY n.id DESC LIMIT {NOTES_FOUND}", proj["id"], f"%{query}%")
        if not rows:
            return f"No notes matching {query!r}."
        return "\n".join(f"- [{r['fn'] or 'project'}] ({r['agent']}, {_ago(r['created_at'])}) {r['text']}" for r in rows)

    # ------------------------------------------------------------- the project as a whole

    def remove_project(self, slug: str) -> int:
        """Delete a project and everything recorded for it. Returns how many functions it had."""
        proj = self._project(slug)
        with self.store.tx() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM functions WHERE project_id=?", (proj["id"],))]
            for table in ("claims", "attempts"):
                db.executemany(f"DELETE FROM {table} WHERE function_id=?", [(i,) for i in ids])
            for table in ("notes", "events", "context_versions", "functions", "symbols", "twin_misses"):
                db.execute(f"DELETE FROM {table} WHERE project_id=?", (proj["id"],))
            db.execute("DELETE FROM projects WHERE id=?", (proj["id"],))
        return len(ids)
