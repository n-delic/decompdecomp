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
from collections import OrderedDict
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
# The functions progress counts (an SQL condition on `functions`): not ones the project sets aside as library code,
# middleware or the C runtime (config/<version>/set_aside.json, objdiff.game_measures), which aren't the game's own
GAME_CODE = "json_extract(meta, '$.set_aside') IS NULL"
# The functions still in the game's build: not one a measure found gone (a piece joined to its function, kept for its
# attempts and notes; ForkEngine.sync marks it), which the map and every count leave out
IN_BUILD = "json_extract(meta, '$.gone') IS NULL"
BRIEF_NOTES = 8  # the newest notes on a function its brief shows
REPEATS_KEPT = 4096  # compile results kept in memory for repeat_of
# events after which earlier compile results don't stand for a repeat (bump_generation): a header, a name, the
# compiler, the project's files changed
GENERATION_EVENTS = frozenset({"context", "rename", "compiler", "split", "files", "sync", "link", "import", "fork",
                               "collab"})
NOTE_CHARS = 4000  # a note longer than this is cut: notes are for findings, not code dumps
NOTES_FOUND = 20  # search_notes' answer at most
# The agent that released a function doesn't get it back automatically for this long, or until its run ends (the
# app writes a 'freed' event when one of its helpers stops). It used to be kept off every function it ever
# released: helper identities are reused by name across runs (claude-haiku-1..8), so on a demo fork four Haiku helpers
# found "nothing left to claim" among exactly the ten functions their names gave up on in earlier runs, while a new
# Opus helper claimed at once. After that the function still comes to it last.
SELF_RELEASE_WINDOW = RELEASE_COOLDOWN
MACHINE_AGENTS = ("autopilot", "permuter")  # decompdecomp's own workers: their claims never mean "given up on"
# "No C makes this" (release_function's not_c): on a DS game five to seven agents in turn took each piece of another
# function, found it couldn't be C and gave it back, saying so in a note that changed nothing. Once this many agents,
# of this many models (one model's blind spot isn't proof), say so, it's set aside: no one is handed it again, and
# the user can give it back (reopen).
NOT_C_AGENTS, NOT_C_MODELS = 3, 2
# The same verdict in agents' notes, from before release_function took one (Network.not_c_from_notes): words that say
# no C at all makes the code. Not a note about compiler flags: "flag-blocked" code may match with other flags.
# On every project of a user (October 2026) no function later matched had three agents' notes saying so.
NOT_C_NOTE = re.compile(
    r"\bany\s+(standalone\s+|plain\s+|valid\s+)?C\b[^.;:]{0,40}?\b(can|could|will|would)\s*(not|n't)\s*"
    r"(produce|emit|express|generate|make|reproduce|compile to|match)"
    r"|\bno\s+(standalone\s+|plain\s+|valid\s+)?C\b[^.;:]{0,40}?\b((can|could|will|would)\s+)?"
    r"(produces?|emits?|express(es)?|generates?|makes?|reproduces?|compiles? to|match(es)?)\b"
    r"|not\s+(expressible|producible|writable|writeable|reproducible|representable|reachable)\s+(in|as|from|with)\s+"
    r"(plain\s+|standalone\s+)?C\b|not\s+C-(expressible|producible|writable|reproducible)"
    r"|cannot\s+be\s+(written|expressed|produced|reproduced|represented)\s+(in|as|from)\s+(plain\s+|standalone\s+)?C\b"
    r"|hand-?written\s+(\w+\s+){0,2}(asm|assembly)|(not|isn't)\s+a\s+(real|standalone|callable)\s+function"
    r"|unmatchable\s+(standalone|in\s+C|from\s+C|as\s+C)", re.I)
# (a CPU's condition flags are no compiler's: "reuses the flags of the cmp" is why some code is hand-written)
FLAG_NOTE = re.compile(r"flag-blocked|\b(compiler|compiler's|project|project's|build|game's|its own|other|different|"
                       r"right|wrong|these|those|default)\s+flags\b|flags?\s+(search|blocked)|-O[0-3s]\b|-framepointer|"
                       r"-mips\d|try_compiler|search_flags|compiler range", re.I)
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
# Prerequisites (release_function's `needs`, the waits table): a function an agent found needs another one first
# (a callee whose convention or body it depends on, a helper the original inlines) waits, claimed after everything
# else, until that one matches; the one it waits for is claimed first meanwhile; once it matches, the waiting one
# comes first, once (its row goes when it's claimed).
# (two functions waiting for each other don't wait: each one's convention came from the other, a caller and its
# callee in one file, and they match together in one batch compile, so the queue mustn't hold both back for good)
WAITING = ("SELECT w.function_id FROM waits w JOIN functions n ON n.id=w.needs_id WHERE n.status != 'matched' "
           "AND NOT EXISTS (SELECT 1 FROM waits w2 WHERE w2.function_id=w.needs_id AND w2.needs_id=w.function_id)")
READY = "SELECT w.function_id FROM waits w JOIN functions n ON n.id=w.needs_id WHERE n.status = 'matched'"
WANTED = "SELECT w.needs_id FROM waits w JOIN functions b ON b.id=w.function_id WHERE b.status = 'open'"
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



def _no_better(scores: list[int | None]) -> int:
    """How many of the last compiles in a row (oldest first in `scores`) didn't beat the best before them."""
    best, since = None, 0
    for s in scores:
        if s is not None and (best is None or s < best):
            best, since = s, 0
        else:
            since += 1
    return since


# A note that says the function matches (written while a function that later stopped matching counted as matched,
# its best score 0): agents read it over the brief's real score and stopped
_SAYS_MATCHED = re.compile(r"already\s+(?:at\s+)?100\s*%|already\s+match(?:es|ed)\b|\b(?:is|was|now)\s+(?:100\s*%|"
                           r"matched\b|a\s+match\b)|100(?:\.0+)?\s*%\s*(?:match|identical)|\bscore\s+0(?![\d.])|✅\s*MATCH|"
                           r"byte[- ]identical", re.I)
_NEGATED = re.compile(r"\b(?:not|n't|never|no|without)\b[^.;]{0,24}$", re.I)


def stale_note(text: str, status: str) -> str:
    """A note as a brief shows it: one saying the function matches, on a function that doesn't, is marked out of
    date (the note itself stays as it was), so the brief's score is the number agents go by."""
    if status == "matched":
        return text
    for m in _SAYS_MATCHED.finditer(text):
        if not _NEGATED.search(text[max(0, m.start() - 40):m.start()]):
            return ("[out of date: written when the function counted as matched; it doesn't match now, so go by the "
                    "score above] " + text)
    return text


# Notes about the setup rather than the function: a build that broke, an agent that stopped or couldn't sign in.
# In one project they took the brief's few note slots (108 "didn't build", 211 "Released because its agent
# stopped", one brief showing the same "didn't build" note twice) while the function's own findings fell off.
_INFRA_NOTE = re.compile(
    r"Released because its agent stopped|didn't build before this change|has to build again|"
    r"FAILED: build\.ninja|rebuilding 'build\.ninja'|subcommand failed|/bin/sh: \d+: \S+: not found|"
    r"nothing can compile|OAuth|Failed to authenticate|not logged in|session limit|usage limit|hit your \w* ?limit",
    re.I)
# A note saying the function can't get closer: true when it was written, until someone beats it (31 of 296 such
# notes in one project: "plain C plateaus here" at 68.3% on a function another agent took to 95.1%)
_CEILING = re.compile(r"plateau|ceiling|not reachable|unreachable|can'?t (?:get|reach|go|be matched|match)|cannot|"
                      r"impossible|dead[- ]end|capped|stuck at|won'?t match|best possible|no way to|maxed", re.I)
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
_SCORE = re.compile(r"\bscore\s*(?:of\s*|=\s*|:\s*)?(\d+)\b", re.I)


def _superseded(text: str, best_score: int | None, best_percent: float | None) -> bool:
    """Whether an older note describes less than the function's best now: it says it can't get closer, or every
    score or match percentage it gives is worse than the best."""
    if _CEILING.search(text):
        return True
    pcts = [float(p) for p in _PERCENT.findall(text)]
    if best_percent is not None and pcts and max(pcts) < best_percent - 0.05:
        return True
    scores = [int(s) for s in _SCORE.findall(text)]
    return best_score is not None and bool(scores) and min(scores) > best_score


def brief_notes(rows, fn, best_at: float | None = None, best_text: str = "", show: int | None = None,
                when: bool = True) -> list[str]:
    """A function's notes as its brief lists them (rows newest first: text, created_at, name). Notes about the
    setup (_INFRA_NOTE) are left out, with a line saying how many; a note given word for word more than once is
    shown once; an older note that said it can't get closer, or described less than the best now (`best_text`,
    reached at `best_at`), is marked superseded and goes after the others. At most `show` (BRIEF_NOTES)."""
    show = BRIEF_NOTES if show is None else show
    best_score = fn["best_score"] if fn["status"] != "matched" else None
    best_percent = None
    if best_score is not None and fn["n_insns"]:
        best_percent = max(0.0, 100.0 * (1 - best_score / (fn["n_insns"] * 100)))
    infra, seen, fresh, stale = 0, {}, [], []
    for n in rows:
        text = (n["text"] or "").strip()
        if _INFRA_NOTE.search(text):
            infra += 1
            continue
        key = " ".join(text.split())
        if key in seen:
            seen[key][1] += 1
            continue
        item = [n, 1, False]
        seen[key] = item
        if (best_at is not None and best_score is not None and (n["created_at"] or 0) < best_at
                and _superseded(text, best_score, best_percent)):
            item[2] = True
            stale.append(item)
        else:
            fresh.append(item)
    lines = []
    for n, count, old in (fresh + stale)[:show]:
        text = stale_note((n["text"] or "").strip(), fn["status"])
        if old:
            text = f"(superseded: best is now {best_text or fn['best_score']}) {text}"
        if count > 1:
            text += f" [noted {count} times]"
        who = f"{n['name']}, {_ago(n['created_at'])}" if when else f"{n['name']}"
        lines.append(f"- ({who}) {text}")
    if infra:
        lines.append(f"- ({infra} note{'s' * (infra != 1)} about builds that broke or agents that stopped left out: "
                     "they say nothing about this function)")
    return lines


@dataclass
class Agent:
    id: int
    name: str
    owner: str | None
    trusted: bool = False


def model_of(name: str) -> str:
    """An agent's model: its name less its number (claude-sonnet-5-5-1: claude-sonnet-5-5)."""
    return re.sub(r"-+\d+$", "", name)  # (some names have two dashes: opencode-minimax-m3.1-flash-free--1)


def set_aside(fn) -> dict:
    """Why a set-aside function is (status asm), for its page: {"set_aside": the reason, "not_c": whether agents
    agreed no C makes it}, or nothing."""
    if fn["status"] != "asm":
        return {}
    meta = json.loads(fn["meta"] or "{}")
    why = meta.get("asm_code") or (f"the same code as {meta['asm_twin']}, a matched assembly function"
                                   if meta.get("asm_twin") else "hand-written assembly")
    return {"set_aside": why, "not_c": "not_c" in meta}

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
    """The budget in words: the prompt and the guides say exactly this. Each function has one number (its brief
    names it, every compile_and_diff result shows where you stand against it, release_function goes by it): the
    prompt used to say "3 compiles ... up to 10", and agents guessed which applied."""
    return ("no better score in N compiles in a row, where N is the function's own number: its brief names it, and "
            "every compile_and_diff result shows where you stand, as in \"no better in 2/6 compiles (move on at 6)\"")


def move_on_brief(fn) -> str:
    """The brief's line naming the function's own move-on number (the one compile_and_diff counts against)."""
    after = stall_budget(fn["n_insns"] or 0)
    return (f"move on after {after} compiles in a row without a better score (this function's number, by its size; "
            f"each compile_and_diff result shows the count)")


def normalized_source(source: str) -> str:
    """Source as repeat compiles compare it: each line without its indentation and trailing spaces, no trailing
    blank lines. Line breaks stay, so __LINE__ and the line numbers a compiler embeds can't differ, and a line
    continuing the one before it (a backslash at its end: a string or macro going on) is kept as it is."""
    out, cont = [], False
    for line in source.splitlines():
        out.append(line if cont else line.strip())
        cont = line.rstrip().endswith("\\")
    return "\n".join(out).rstrip("\n")


def stall_line(since: int, after: int) -> str:
    """Where an agent stands against a function's move-on number, as every compile result and the release push-back
    word it (one wording, so the two can't seem to disagree)."""
    return f"no better in {since}/{after} compiles (move on at {after})"


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
        # (agent id, function id) -> its last compile's diff rows and their legend, for last_diff
        self._last_diffs: dict[tuple[int, int], tuple[list, str]] = {}
        self._generation: dict[int, int] = {}  # project id -> changes compiles depend on (bump_generation)
        self._compiled: OrderedDict[str, dict] = OrderedDict()  # compile_key -> result, for repeat_of

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
        if kind in GENERATION_EVENTS and project_id is not None:
            self.bump_generation(project_id)

    # ------------------------------------------------------- repeat compiles

    def bump_generation(self, project_id: int) -> None:
        """Something every compile of the project can depend on changed (a header, a name, the compiler, a file
        split, an edit_file): earlier compile results no longer stand for a repeat of the same source."""
        self._generation[project_id] = self._generation.get(project_id, 0) + 1

    def compile_key(self, fn, *state: str) -> str:
        """What a compile's result depends on besides the source: the function, the project's generation and `state`
        (the header, the file around the function...). Sources are compared without per-line indentation and
        trailing spaces (line numbers stay, for __LINE__)."""
        h = hashlib.sha1(f"{fn['id']}\0{self._generation.get(fn['project_id'], 0)}".encode())
        for part in state:
            h.update(b"\0" + normalized_source(part).encode("utf-8", "replace"))
        return h.hexdigest()

    def repeat_of(self, key: str, agent: Agent, fn) -> dict | None:
        """An earlier compile of the same source in the same state: its result again, marked as a repeat, without
        building anything or counting toward the move-on number (in one project, 3,639 of 9,611 compiles were
        byte-identical to an earlier one of the same function)."""
        hit = self._compiled.get(key)
        if hit is None:
            return None
        self._compiled.move_to_end(key)
        who = "your" if hit["agent_id"] == agent.id else f"{self._agent_name(hit['agent_id'])}'s"
        head = (f"🔁 REPEAT: this source is the same (indentation aside) as {who} compile #{hit['n']} of {fn['name']} "
                f"({_ago(hit['at'])}), and nothing it depends on changed since, so it wasn't built again and doesn't "
                "count toward the move-on number. That compile's result:\n")
        return {"text": head + hit["text"] + self.stall_note(fn["id"], agent.id), "score": hit["score"],
                "matched": False, "repeat": True}

    def remember_compile(self, key: str, agent: Agent, fn, text: str, score: int | None) -> None:
        """A compile's result (without its move-on line), for repeat_of."""
        n = self.store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND agent_id=?", fn["id"],
                           agent.id)["n"]
        self._compiled[key] = {"text": text, "score": score, "agent_id": agent.id, "n": n, "at": time.time()}
        while len(self._compiled) > REPEATS_KEPT:
            self._compiled.popitem(last=False)

    def same_score_note(self, fn_id: int, agent_id: int, score: int | None, source: str) -> str:
        """When a new variant only reproduces a score an earlier, different source of this claim had: which compile
        it equals (in one project, 21 of 56 compiles in two long sessions did, 1001 seven times on one function)."""
        st = self.stall_status(fn_id, agent_id)
        if score is None or st["claimed_at"] is None:
            return ""
        rows = self.store.all("SELECT score, source FROM attempts WHERE function_id=? AND agent_id=? AND created_at >= ? "
                              "ORDER BY id", fn_id, agent_id, st["claimed_at"])
        mine = normalized_source(source)
        for k, r in enumerate(rows[:-1], 1):  # (the last row is this compile)
            if r["score"] == score and normalized_source(r["source"] or "") != mine:
                return (f"\nSame score as your compile #{k} of this claim ({score}) with a different source: this "
                        "change made no difference the judge sees. Try a different kind of change.")
        return ""

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
        still = self.still_holding(agent, function)
        fn = self.claim_row(agent, project, function, strategy)
        if fn is None:
            return self.nothing_to_claim(agent, self._project(project))
        return still + self._brief(fn, agent, header="CLAIMED")

    def still_holding(self, agent: Agent, function: str | None) -> str:
        """What to say first when claim_function (no function named) gives an agent the one it already holds: after
        a pushed-back release it still does, and an agent read the same brief coming back as the queue handing it
        back the function it had just released."""
        if function:
            return ""
        held = self.store.one("SELECT f.name, c.claimed_at FROM claims c JOIN functions f ON f.id=c.function_id "
                              "WHERE c.agent_id=?", agent.id)
        if held is None or not self.store.one(
                "SELECT 1 FROM events WHERE agent_id=? AND kind='release_pushed' AND detail=? AND created_at >= ?",
                agent.id, held["name"], held["claimed_at"] or 0):
            return ""  # an ordinary renewal of the claim
        return (f"You still hold {held['name']}: it wasn't released (release_function was pushed back), so this is "
                "your claim on it, not a new one. Compile the next step; release_function goes through once you've "
                "compiled it, when you ask again.\n\n")

    def _own_release_cutoff(self, db, agent_id: int, now: float) -> float:
        """Releases by this agent after this time keep it off those functions: the last SELF_RELEASE_WINDOW, and
        only since its run started (its last 'freed' event, release_stopped's, or 'stopped', a helper's own)."""
        row = db.execute("SELECT MAX(created_at) AS t FROM events WHERE agent_id=? AND kind IN ('freed', 'stopped')",
                         (agent_id,)).fetchone()
        return max(now - SELF_RELEASE_WINDOW, (row["t"] if row else None) or 0)

    def nothing_to_claim(self, agent: Agent, proj, scope: str = "") -> str:
        """What a claim that found nothing tells the agent: whether the open functions are held (by whom, for how
        long), ones it set aside itself (which it can still take by name), or really all done. A bare "nothing left"
        sent four helpers away from a project with ten open functions."""
        now = time.time()
        n_open = self.store.one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='open'",
                                proj["id"])["n"]
        if not n_open:
            return f"Nothing left to claim in {proj['slug']}: every function{scope} matches. 🎉"
        held = self.store.all(
            "SELECT f.name, a.name AS agent, c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id "
            "LEFT JOIN agents a ON a.id=c.agent_id WHERE f.project_id=? AND f.status='open' ORDER BY c.expires_at",
            proj["id"])
        with self.store.tx() as db:
            cutoff = self._own_release_cutoff(db, agent.id, now)
        mine = self.store.all(
            "SELECT DISTINCT f.name FROM functions f JOIN events e ON e.project_id=f.project_id AND e.detail=f.name "
            "WHERE f.project_id=? AND f.status='open' AND e.agent_id=? AND e.kind='release' AND e.created_at > ? "
            "AND f.id NOT IN (SELECT function_id FROM claims) ORDER BY f.name", proj["id"], agent.id, cutoff)
        lines = [f"Nothing to claim in {proj['slug']} right now: {n_open} function{'s' * (n_open != 1)} "
                 f"{'are' if n_open != 1 else 'is'} open, but none is free for you."]
        if held:
            shown = ", ".join(f"{r['name']} ({r['agent'] or '?'}, {max(0, int(r['expires_at'] - now)) // 60} min left)"
                              for r in held[:10])
            lines.append(f"{len(held)} held by other agents: {shown}{', ...' if len(held) > 10 else ''}. Each is "
                         "free again when its agent matches or releases it, or when its lease runs out.")
        if mine:
            names = [r["name"] for r in mine]
            lines.append(f"{len(names)} you released yourself in the last {SELF_RELEASE_WINDOW // 60} minutes: "
                         f"{', '.join(names[:10])}{', ...' if len(names) > 10 else ''}. They come back to you later. "
                         f"To take one now, call claim_function with function=\"{names[0]}\".")
        return "\n".join(lines)

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
                    want = self._function(function, proj["slug"] if proj else None)["name"]
                    raise NetworkError(f"you already hold {held['name']}; match it or release_function it first (if "
                                       f"{held['name']} needs {want} done first, release_function with "
                                       f"needs=\"{want}\": that records it and hands you {want})")
                fn = held
                db.execute("UPDATE claims SET expires_at=? WHERE function_id=?", (now + self.claim_ttl, fn["id"]))
            elif function:
                fn = self._function(function, proj["slug"] if proj else None)
                if fn["status"] == "matched":
                    raise NetworkError(f"{fn['name']} is already matched; use get_function to read its source")
                if fn["status"] == "asm":
                    raise NetworkError(f"{fn['name']} is set aside, as no C makes it ({set_aside(fn)['set_aside']}): "
                                       "claim another")
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
                # not what this agent released lately (see SELF_RELEASE_WINDOW); what it released before that last
                # functions waiting for another to match come last (WAITING); the ones they wait for, and those whose
                # wait is over, first (after what the release rules put back, before model routing's escalations)
                query = (
                    f"""SELECT f.* FROM functions f LEFT JOIN {_REGION} r ON r.region = f.vram / {REGION_BYTES}
                        WHERE f.project_id=:pid AND f.status='open' AND (:weak = 0 OR f.abandons < :escalate)
                        AND f.id NOT IN (SELECT function_id FROM claims)
                        AND f.name NOT IN (SELECT detail FROM events WHERE project_id=:pid AND agent_id=:agent
                                           AND kind='release' AND created_at > :mine)
                        ORDER BY f.id IN ({WAITING}),
                                 f.name IN (SELECT detail FROM events WHERE project_id=:pid AND agent_id=:agent
                                            AND kind='release'),
                                 f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                            AND agent_id IN (SELECT value FROM json_each(:kin)) AND created_at >
                                            COALESCE((SELECT MAX(created_at) FROM events WHERE project_id=:pid
                                                      AND kind IN ('context', 'compiler')), 0)),
                                 f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                            AND created_at > :cooled),
                                 f.id IN ({READY}) DESC, f.id IN ({WANTED}) DESC,
                                 :strong AND f.abandons >= :escalate DESC, {STRATEGIES[strategy]} LIMIT 1""")
                args = {"pid": proj["id"], "agent": agent.id, "kin": json.dumps(kin), "cooled": now - RELEASE_COOLDOWN,
                        "overall": overall, "escalate": ESCALATE_AFTER, "weak": route == "weak", "strong": route == "strong",
                        "mine": self._own_release_cutoff(db, agent.id, now)}
                fn = db.execute(query, args).fetchone()
                if fn is None and route == "weak":  # only what stronger models are for is left: better tried than idle
                    fn = db.execute(query, args | {"weak": False}).fetchone()
                if fn is None:
                    return None
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
            # a wait that's over has had its turn at the front once it's claimed
            db.execute("DELETE FROM waits WHERE function_id=? AND needs_id IN (SELECT id FROM functions "
                       "WHERE status='matched')", (fn["id"],))
        return fn

    def _route(self, db, project_id: int, agent: Agent, now: float) -> str | None:
        """"weak" for a model at work in the project well behind the strongest one at work, "strong" for that one
        while a weak one works too (see ESCALATE_AFTER), None otherwise: no routing until both are told apart by
        ROUTE_MIN_CLAIMS claims each."""
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM agents")}
        claims, won, busy = collections.Counter(), collections.Counter(), {model_of(agent.name)}
        for r in db.execute("SELECT agent_id, COUNT(*) AS n, MAX(created_at) AS last FROM events WHERE project_id=? "
                            "AND kind='claim' GROUP BY agent_id", (project_id,)):
            if names.get(r["agent_id"], MACHINE_AGENTS[0]) not in MACHINE_AGENTS:
                claims[model_of(names[r["agent_id"]])] += r["n"]
                if r["last"] > now - ROUTE_ACTIVE_SECONDS:
                    busy.add(model_of(names[r["agent_id"]]))
        for r in db.execute("SELECT matched_agent_id AS id, COUNT(*) AS n FROM functions WHERE project_id=? "
                            "AND status='matched' GROUP BY matched_agent_id", (project_id,)):
            if names.get(r["id"], MACHINE_AGENTS[0]) not in MACHINE_AGENTS:
                won[model_of(names[r["id"]])] += r["n"]
        overall = sum(won.values()) / sum(claims.values()) if sum(claims.values()) else 0.5
        share = {m: (won[m] + ROUTE_PRIOR * overall) / (claims[m] + ROUTE_PRIOR) for m in busy
                 if claims[m] >= ROUTE_MIN_CLAIMS}
        mine = model_of(agent.name)
        if mine not in share or len(share) < 2:
            return None
        best = max(share.values())
        if share[mine] < WEAK_SHARE * best:
            return "weak"
        weak = any(s < WEAK_SHARE * best for s in share.values())
        return "strong" if share[mine] == best and weak else None

    def release(self, agent: Agent, function: str, notes: str, project: str | None = None, not_c: bool = False) -> str:
        """An agent gives a function back: its claim ends, its notes stay for the next agent, and the function
        counts one more give-up (the claim order puts those later). `not_c`: the agent found no C makes its code
        (NOT_C_AGENTS)."""
        fn = self._function(function, project)
        if not_c and not notes.strip():
            raise NetworkError("say in notes why no C makes it: the next agents and the user read it")
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
            said = self._not_c(db, fn, agent, notes.strip()) if not_c else ""
        return (f"Released {fn['name']}. Your best attempt and notes are saved so the next agent starts "
                f"where you left off.{said} Call claim_function for a new one.")

    def _not_c(self, db, fn, agent: Agent, why: str) -> str:
        """An agent's verdict that no C makes fn's code; with NOT_C_AGENTS agents of NOT_C_MODELS models agreeing, fn
        is set aside (status asm). Returns what to tell the agent."""
        meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (fn["id"],)).fetchone()["meta"] or "{}")
        said = meta.setdefault("not_c", {})
        said[agent.name] = {"model": model_of(agent.name), "why": why[:NOTE_CHARS], "t": time.time()}
        models = sorted({v["model"] for v in said.values()})
        if len(said) < NOT_C_AGENTS or len(models) < NOT_C_MODELS:
            db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), fn["id"]))
            return (f" Your verdict that no C makes it is counted ({len(said)} of {NOT_C_AGENTS} agents, "
                    f"{len(models)} of {NOT_C_MODELS} models): it's set aside once enough agree.")
        meta["asm_code"] = f"{len(said)} agents ({', '.join(models)}) found no C makes it: {why}"
        db.execute("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", (json.dumps(meta), fn["id"]))
        self._event(db, fn["project_id"], agent.id, "not_c",
                    f"{fn['name']}: set aside, {len(said)} agents of {len(models)} models found no C makes it")
        return f" With yours, {len(said)} agents of {len(models)} models agree no C makes it: it's set aside now."

    def not_c_from_notes(self, slug: str) -> int:
        """Open functions agents' notes agree no C makes (NOT_C_NOTE: NOT_C_AGENTS agents of NOT_C_MODELS models) set
        aside as the verdict would: a project whose agents said so before release_function took a verdict (a DS game:
        26 functions, each confirmed by six agents in turn) gets them out of the way when it's checked again. Never a
        function the user gave back. Returns how many."""
        pid = self._project(slug)["id"]
        said: dict[int, dict] = collections.defaultdict(dict)
        # notes from before pieces were joined to a function were about a piece, not about it (decompdecomp's note
        # on a join says so): they don't count
        joined = {r["function_id"]: r["t"] for r in self.store.all(
            "SELECT n.function_id, MAX(n.created_at) AS t FROM notes n JOIN agents a ON a.id=n.agent_id WHERE "
            "n.project_id=? AND a.name='decompdecomp' AND n.text LIKE '%joined%' GROUP BY n.function_id", pid)}
        for r in self.store.all("SELECT n.function_id, n.text, n.created_at, a.name FROM notes n JOIN agents a "
                                "ON a.id=n.agent_id JOIN functions f ON f.id=n.function_id WHERE f.project_id=? AND "
                                "f.status='open' AND (f.meta IS NULL OR f.meta NOT LIKE '%\"reopened\"%') ORDER BY n.id",
                                pid):
            if r["name"] in ("you", "decompdecomp", *MACHINE_AGENTS) or r["created_at"] <= joined.get(r["function_id"], float("-inf")) \
                    or not NOT_C_NOTE.search(r["text"]) or FLAG_NOTE.search(r["text"]):
                continue
            said[r["function_id"]][r["name"]] = {"model": model_of(r["name"]), "why": r["text"][:NOTE_CHARS],
                                                 "t": r["created_at"], "from": "note"}
        n = 0
        with self.store.tx() as db:
            for fid, verdicts in said.items():
                models = sorted({v["model"] for v in verdicts.values()})
                if len(verdicts) < NOT_C_AGENTS or len(models) < NOT_C_MODELS:
                    continue
                meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (fid,)).fetchone()["meta"] or "{}")
                why = max(verdicts.values(), key=lambda v: v["t"])["why"].split("\n")[0][:300]
                meta["not_c"] = {**meta.get("not_c", {}), **verdicts}
                meta["asm_code"] = f"{len(verdicts)} agents ({', '.join(models)}) said in their notes that no C makes it: {why}"
                db.execute("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", (json.dumps(meta), fid))
                name = db.execute("SELECT name FROM functions WHERE id=?", (fid,)).fetchone()["name"]
                self._event(db, pid, None, "not_c", f"{name}: set aside, {len(verdicts)} agents of {len(models)} models "
                                                    "said in their notes that no C makes it")
                n += 1
        return n

    def set_aside_by_you(self, function: str, project: str | None = None, why: str = "") -> None:
        """The user sets a function aside: no C makes it (hand-written assembly, a piece of another function). Their
        word needs no agents agreeing (theirs is the project); "Give it back to the agents" undoes it. Never
        "matched": only the judge says that."""
        fn = self._function(function, project)
        if fn["status"] != "open":
            raise NetworkError(f"{fn['name']} isn't open (it's {fn['status']}): only an open function can be set aside")
        why = why.strip() or "no C makes it"
        meta = {k: v for k, v in json.loads(fn["meta"] or "{}").items() if k != "reopened"}
        meta["not_c"] = {**meta.get("not_c", {}), "you": {"model": "you", "why": why[:NOTE_CHARS], "t": time.time()}}
        meta["asm_code"] = f"you set it aside: {why}"
        with self.store.tx() as db:
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            db.execute("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", (json.dumps(meta), fn["id"]))
            self._event(db, fn["project_id"], None, "not_c", f"{fn['name']}: set aside by you ({why[:120]})")

    def reopen(self, function: str, project: str | None = None) -> None:
        """The user gives a set-aside function back to the agents (they think C makes it after all): the agents'
        verdicts on it are forgotten, and no check sets it aside again."""
        fn = self._function(function, project)
        meta = {k: v for k, v in json.loads(fn["meta"] or "{}").items() if k not in ("not_c", "asm_code", "asm_twin")}
        with self.store.tx() as db:
            db.execute("UPDATE functions SET status='open', meta=? WHERE id=? AND status='asm'",
                       (json.dumps({**meta, "reopened": True}), fn["id"]))
            self._event(db, fn["project_id"], None, "reopen", f"{fn['name']}: open again (you gave it back to the agents)")

    def release_stopped(self, agent_ids, why: str) -> list[str]:
        """The claims of agents whose process is gone, released at once with a note (`why`), instead of holding their
        functions until CLAIM_TTL runs out: after an update or a restart of the app, every open function looked held
        by helpers that had died with it. Not counted as giving up. Each function freed gets a 'freed' event (one
        without a function when the agent held none), which ends the agent's run: what it released before no longer
        keeps it off those functions (SELF_RELEASE_WINDOW). Returns the functions released."""
        now, freed = time.time(), []
        with self.store.tx() as db:
            own = {r["id"] for r in db.execute("SELECT id FROM agents WHERE name IN (?, ?)", MACHINE_AGENTS)}
            for aid in agent_ids:
                rows = db.execute("SELECT f.id, f.project_id, f.name FROM claims c JOIN functions f ON f.id=c.function_id "
                                  "WHERE c.agent_id=?", (aid,)).fetchall()
                db.execute("DELETE FROM claims WHERE agent_id=?", (aid,))
                for r in rows:
                    if aid not in own:  # decompdecomp's own tries leave no notes
                        db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)",
                                   (r["project_id"], r["id"], aid, f"Released because its agent stopped ({why}); its "
                                    "attempts are kept.", now))
                    self._event(db, r["project_id"], aid, "freed", r["name"])
                    freed.append(r["name"])
                if not rows:
                    self._event(db, None, aid, "freed", "")
        return freed

    def release_local_claims(self, why: str) -> list[str]:
        """When the app starts: none of its own helpers (origin 'local') is running yet, so whatever they hold is left
        over from the last run. Agents that run elsewhere (a room's members, an agent using the MCP server) may still
        be working: theirs keep the CLAIM_TTL rule; so do the user's own claims and decompdecomp's workers'."""
        ids = [r["id"] for r in self.store.all("SELECT id FROM agents WHERE origin='local' AND name NOT IN (?, ?, ?)",
                                               "you", *MACHINE_AGENTS)]
        return self.release_stopped(ids, why)

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
        if fn["status"] != "matched":
            lines.append(move_on_brief(fn))
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
        notes = self.notes_for_brief(fn)
        if notes:
            lines += ["", "### Notes from previous agents on this function", *notes]
        lines += self.dependency_brief(fn)
        if fn["unit"] and fn["unit"] in self._unit_contexts(proj["id"]):
            lines.insert(2, f"source unit {fn['unit']}: call get_context with function=\"{fn['name']}\" for its header")
        if header == "CLAIMED":
            lines += ["", self._next_step(proj, ctx)]
        return "\n".join(lines)

    def notes_for_brief(self, fn, best_text: str = "", show: int | None = None, when: bool = True) -> list[str]:
        """A function's notes for its brief (brief_notes), with when its best score was first reached."""
        rows = self.store.all("SELECT n.text, n.created_at, a.name FROM notes n LEFT JOIN agents a ON a.id=n.agent_id "
                              f"WHERE n.function_id=? ORDER BY n.id DESC LIMIT {BRIEF_NOTES * 8}", fn["id"])
        best_at = None
        if fn["status"] != "matched" and fn["best_score"] is not None:
            row = self.store.one("SELECT MIN(created_at) AS t FROM attempts WHERE function_id=? AND score=?",
                                 fn["id"], fn["best_score"])
            best_at = row["t"] if row else None
        return brief_notes(rows, fn, best_at, best_text, show, when)

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
        key = self.compile_key(fn, ctx["header"], str(ctx["version"]), proj["config"] or "", source)
        if not scratch and (again := self.repeat_of(key, agent, fn)) is not None:
            return again
        result = self._compile(proj, ctx["header"], source, fn)

        if not result.ok:
            self._record(fn, agent, None, source, ctx["version"])
            text = f"❌ COMPILE ERROR (context v{ctx['version']})\n{result.log.strip()}"
            self.remember_compile(key, agent, fn, text, None)
            return {"text": text, "score": None, "matched": False}

        cand, verdict, problems = self._evaluate(fn, proj, result.obj)
        if cand is None:
            self._record(fn, agent, None, source, ctx["version"])
            text = (f"❌ Compiled, but the object has no function named `{fn['name']}`. "
                    f"Define it with exactly that name (non-static).")
            self.remember_compile(key, agent, fn, text, None)
            return {"text": text, "score": None, "matched": False}
        d = scoring.diff(self._target_obj(fn, proj), cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        matched_now = d.bytes_match and not scratch
        last = self.store.one("SELECT score FROM attempts WHERE function_id=? AND agent_id=? AND score IS NOT NULL "
                              "ORDER BY id DESC LIMIT 1", fn["id"], agent.id)
        self._record(fn, agent, d.score, source, ctx["version"], matched=matched_now)
        self.keep_diff(agent.id, fn["id"], d.rows)

        if scratch:
            head = f"(scratch — {fn['name']} was already matched) score {d.score}"
        elif d.bytes_match:
            return {"text": f"✅ MATCH! {fn['name']} is byte-identical to the target ({fn['n_insns']} instructions). "
                            f"Credited to {agent.name}; your claim is released. Call claim_function for the next one."
                            f"{self.dependents_note(fn['id'])}",
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
        text = f"{head}\n{d.render()}{warn}"
        if not scratch:
            self.remember_compile(key, agent, fn, text, d.score)
            text += self.same_score_note(fn["id"], agent.id, d.score, source)
        return {"text": text + self.stall_note(fn["id"], agent.id), "score": d.score, "matched": False}

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

    def keep_diff(self, agent_id: int, function_id: int, rows: list, legend: str = scoring.LEGEND) -> None:
        """A compile's diff rows, kept for last_diff (memory only: the next compile replaces them)."""
        self._last_diffs[(agent_id, function_id)] = (list(rows), legend)

    def last_diff(self, agent: Agent, function: str, project: str | None = None, context: int = 2) -> str:
        """The full diff of this agent's last compile of a function, every differing row: compile_and_diff shows the
        first 60 rows and cut the rest (27 to 181 rows in one project), and the only way to see them was another
        compile. Costs no compile."""
        fn = self._function(function, project)
        kept = self._last_diffs.get((agent.id, fn["id"]))
        if kept is None:
            raise NetworkError(f"no compile of {fn['name']} by you since the app started: compile_and_diff first")
        rows, legend = kept
        context = max(0, min(int(context or 0), 10))
        return (f"Full diff of your last compile of {fn['name']} ({len(rows)} rows; not a new compile):\n"
                + scoring.render_rows(rows, context, max_rows=len(rows) * (2 * context + 2) + 10, run=len(rows) + 1,
                                      legend=legend, more_hint=False))

    def stall_status(self, function_id: int, agent_id: int) -> dict:
        """Where an agent stands against a function's move-on number: one count for compile_and_diff's results,
        the brief and release_function. The compiles of its current claim (with no claim, its last
        STALL_LOOKBACK). Compile results once counted the agent's last compiles across claims while release_function
        counted the claim's and said "no better in 8 compiles in a row" whatever the count was: in one project a
        release was refused with the last 4 of 7 no better, then let through after exactly one more compile, so
        agents spent a compile to get past the check (446 push-backs, 36 of them improved after it)."""
        row = self.store.one("SELECT n_insns FROM functions WHERE id=?", function_id)
        after = stall_budget((row["n_insns"] if row else 0) or 0)
        claim = self.store.one("SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?", function_id, agent_id)
        if claim is not None:
            start = claim["claimed_at"] or 0
            rows = self.store.all("SELECT score FROM attempts WHERE function_id=? AND agent_id=? AND created_at >= ? "
                                  "ORDER BY id", function_id, agent_id, start)
            scores = [r["score"] for r in rows]
        else:
            start = None
            rows = self.store.all(f"SELECT score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC "
                                  f"LIMIT {STALL_LOOKBACK}", function_id, agent_id)
            scores = [r["score"] for r in reversed(rows)]
        return {"claimed_at": start, "compiles": len(scores), "no_better": _no_better(scores), "after": after,
                "best": min((s for s in scores if s is not None), default=None)}

    def stall_note(self, function_id: int, agent_id: int) -> str:
        """Every compile result's last line: where the agent stands against the function's move-on number and, once
        it's reached, the nudge to release: functions that match mostly do in the first few tries, and the agent's
        time is better spent on the next one. Big functions get longer (a quarter of Mario Party 6's agent matches
        over 64 instructions took more than 4 compiles, and those functions hold most of the code left):
        STALL_BUDGET. release_function lets a release through exactly when this says to move on."""
        st = self.stall_status(function_id, agent_id)
        if not st["compiles"]:
            return ""
        since, after = st["no_better"], st["after"]
        if since < after:
            return f"\n\nMove-on count: {stall_line(since, after)}."
        return (f"\n\n⏭ Move on: {stall_line(since, after)}. release_function with notes (what you tried, what's "
                "left) goes through now; then claim the next one: functions that match mostly do in the first few "
                "tries, and this one keeps your best attempt for whoever comes next.")

    def claim_progress(self, agent: Agent, fn) -> dict | None:
        """This agent's claim on `fn` as release_function judges it: stall_status, the same count compile_and_diff
        shows. None: no claim."""
        st = self.stall_status(fn["id"], agent.id)
        return None if st["claimed_at"] is None else st

    # ---------------------------------------------------------- prerequisites

    def wait_for(self, agent: Agent, fn, needs) -> None:
        """Record that `fn` needs `needs` done first (see WAITING)."""
        with self.store.tx() as db:
            db.execute("INSERT OR REPLACE INTO waits(function_id, needs_id, agent_id, created_at) VALUES(?,?,?,?)",
                       (fn["id"], needs["id"], agent.id, time.time()))
            self._event(db, fn["project_id"], agent.id, "waits", f"{fn['name']} -> {needs['name']}")

    def dependency_brief(self, fn) -> list[str]:
        """For a brief: what this function waits for, and what waits for it."""
        out = []
        row = self.store.one("SELECT n.name, n.status, a.name AS agent FROM waits w JOIN functions n ON n.id=w.needs_id "
                             "LEFT JOIN agents a ON a.id=w.agent_id WHERE w.function_id=?", fn["id"])
        if row is not None and fn["status"] != "matched":
            if row["status"] == "matched":
                out += ["", f"### {row['name']} matches now: {row['agent'] or 'an agent'} found this function needs it "
                            f"first (a callee or an inlined helper): read its code (get_function) and use it here."]
            elif self.store.one("SELECT 1 FROM waits WHERE function_id=(SELECT id FROM functions WHERE name=? AND "
                                "project_id=?) AND needs_id=?", row["name"], fn["project_id"], fn["id"]):
                out += ["", f"### Needs {row['name']}, which needs this one: they depend on each other (a caller and its "
                            "callee, each one's convention coming from the other), so work on them together: "
                            f"compile_and_diff(function=\"{fn['name']}, {row['name']}\", source=both definitions)."]
            else:
                out += ["", f"### Waits for {row['name']}: {row['agent'] or 'an agent'} found this function needs it "
                            f"first (a callee or an inlined helper), so it's claimed after everything else until "
                            f"{row['name']} matches. Work on it if you can; else release_function with "
                            f"needs=\"{row['name']}\"."]
        waiting = self.store.all("SELECT b.name FROM waits w JOIN functions b ON b.id=w.function_id WHERE w.needs_id=? "
                                 "AND b.status='open' ORDER BY b.name LIMIT 8", fn["id"])
        if waiting and fn["status"] != "matched":
            out += ["", f"### Waited for by {', '.join(r['name'] for r in waiting)}: they need this one first, so "
                        "matching it unblocks them."]
        return out

    def dependents_note(self, function_id: int) -> str:
        """For a match: the functions that were waiting for it."""
        rows = self.store.all("SELECT b.name FROM waits w JOIN functions b ON b.id=w.function_id WHERE w.needs_id=? "
                              "AND b.status='open' ORDER BY b.name LIMIT 6", function_id)
        if not rows:
            return ""
        return (f"\nWaiting for this one: {', '.join(r['name'] for r in rows)}. They come first in the queue now; "
                f"claim_function(function=\"{rows[0]['name']}\") takes one next.")

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
