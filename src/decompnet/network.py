"""The coordination core under the app's engine (app/engine): who works on what, what counts as a match, and what
everyone has learned.

Every public method here is one agent-facing action. They return plain text because the consumer is an LLM reading
a tool result.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import agent_notes, agents, scoring
from .candidate_work import trial
from .compiler import compile_source
from .elfobj import FunctionObj, Reloc, extract_function
from .project import compiler_config, compiler_for
from .release_receipts import operation as release_operation
from .request_timing import build as timed_build
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
# The game's own code (an SQL condition on `functions`): not what the project sets aside as library code, middleware
# or the C runtime (config/<version>/set_aside.json, objdiff.game_measures; while its scope is the game's own code).
# (meta's set_aside, as store.py's triggers copy it: the counts' index holds it)
GAME_CODE = "aside_mark IS NULL"
# Set aside, for either reason (an SQL condition on `functions`): no C makes it (status asm: hand-written assembly, a
# piece of a function, agents' verdict) or it isn't the game's own (GAME_CODE). Every count takes it off the total
# and off what matched, so a matched library function doesn't count either.
SET_ASIDE = f"(status = 'asm' OR NOT ({GAME_CODE}))"
# The counts a project's page and the project list show (functions, matched, bytes, set aside), as SQL columns
PROGRESS_COLUMNS = (
    "COUNT(*) AS total, SUM(status='matched' AND NOT {a}) AS matched, SUM(size) AS bytes, "
    "SUM(CASE WHEN status='matched' AND NOT {a} THEN size ELSE 0 END) AS matched_bytes, SUM({a}) AS set_aside, "
    "SUM(CASE WHEN {a} THEN size ELSE 0 END) AS set_aside_bytes").format(a=SET_ASIDE)
# The functions still in the game's build: not one a measure found gone (a piece joined to its function, kept for its
# attempts and notes; ForkEngine.sync marks it), which the map and every count leave out (meta's gone, copied too)
IN_BUILD = "gone_mark IS NULL"
BRIEF_NOTES = 8  # the newest notes on a function its brief shows
REPEATS_KEPT = 4096  # compile results kept in memory for repeat_of
# events after which earlier compile results don't stand for a repeat (bump_generation): a header, a source file, a
# name, the compiler, the project's files changed
GENERATION_EVENTS = frozenset({"context", "source", "rename", "compiler", "split", "files", "sync", "link", "import",
                               "fork", "collab"})
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

def assembly_blocker(why):
    """Tool/compiler limitations do not establish that a target was handwritten assembly."""
    if FLAG_NOTE.search(why):
        return 'Compiler/build settings need review; unsuitable flags are not proof that no C exists.'
    extension = re.search(r'asm|extension|builtin|register|return.address|\$31|\$ra',why,re.I)
    rejected = re.search(r'unsupported|not supported|reject|forbid|disallow|tool(?:ing)?.{0,30}restrict',why,re.I)
    return_address = re.search(r'__builtin_return_address|return.address',why,re.I)
    if extension and (rejected or return_address):
        return 'Compiler-extension or return-address support needs review; a tooling restriction is not proof of handwritten assembly.'
    return None


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
# The value orders only the twins that come first: one given up on waits by size like any other function. Counted for
# every twin, it put twins given up on ahead of every untried function: overnight on a game, eight agents passed 41
# two-instruction twins no C compiles to around, each one back after every RELEASE_COOLDOWN (1,025 turns, no match).
_TWIN_FIRST = f"(f.boost > 0 AND f.n_insns <= {TWIN_MAX_INSNS} AND f.abandons < {TWIN_MAX_ABANDONS})"
_TWINS = (f"{_TWIN_FIRST} DESC, "
          f"CASE WHEN {_TWIN_FIRST} THEN (f.boost + 1) * {TWIN_VALUE_SCALE} / (f.n_insns + {TWIN_SIZE_FLOOR}) ELSE 0 "
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
# (a failing part waits even for its twins: they'd fail the same way)
_EASIEST = (f"{_FAILING}, " + _TWINS + f"f.abandons > 0, (f.n_insns + {SIZE_STEP - 1}) / {SIZE_STEP} ASC, "
            f"{_SHARE} DESC, f.n_insns ASC")
STRATEGIES = {
    "no_compiling_c": "f.attempts ASC, f.n_insns ASC",
    "oldest_checked": "COALESCE((SELECT MAX(t.created_at) FROM attempts t WHERE t.function_id=f.id),0) ASC, f.id ASC",
    "least_tried": "f.attempts ASC, f.n_insns ASC",
    "easiest": _EASIEST,
    # what nobody gave up on, small first; among functions of about the same size, the files closest to done (a
    # file is linked into the game once all of it matches). Leftovers agents released again and again come last,
    # or a crowd of agents spends its time passing the hardest functions around.
    "finish": f"{_FAILING}, " + _TWINS + f"f.abandons ASC, (f.n_insns + {FINISH_SIZE_STEP - 1}) / {FINISH_SIZE_STEP} ASC, "
                       "(SELECT COUNT(*) FROM functions g WHERE "
                       "g.project_id=f.project_id AND g.unit=f.unit AND g.status='open') ASC, f.n_insns ASC",
    # near-misses, the ones given up on least first (else every agent resumes the same stuck one)
    "closest": "f.best_score IS NULL, f.abandons ASC, CAST(f.best_score AS REAL) / (f.n_insns * 100) ASC",
    "hardest": "f.n_insns DESC",
    # a first pass over everything before polishing near misses: what nobody compiled yet, then easiest's order
    "untried": f"f.attempts > 0, {_EASIEST}",
    "random": "RANDOM()",
}
# What a project's owner can choose for the order helpers take functions in (its config's "claim_order"): each names
# the strategy it uses and the sentence agents read. Absent (""): the agent's own strategy, as before.
CLAIM_ORDERS = {
    "easiest": "smallest functions first",
    "hardest": "largest functions first",
    "closest": "functions closest to matching first",
    "untried": "functions nobody has tried yet first, then the smallest",
}


class NetworkError(Exception):
    """An error meant to be shown to the agent as-is."""



def _no_better(scores: list[int | None], baseline: int | None = None) -> int:
    """How many of the last compiles in a row (oldest first in `scores`) didn't beat the best before them."""
    best, since = baseline, 0
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
                           r"byte[- ]identical|"
                           # "Matched 100% with plain C", "Matches 100% via compile_and_diff", "Compiles to 100% every
                           # time ... don't spend compiles on it": on an Xbox game a judge that misread units' objects
                           # gave score 0, and these sent later agents away from functions at 83-98%
                           r"\b(?:match(?:es|ed)?|compiles?\s+to)\s+(?:at\s+)?100\s*%", re.I)
_NEGATED = re.compile(r"\b(?:not|n't|never|no|without)\b[^.;]{0,24}$", re.I)


def says_matched(text: str) -> bool:
    """Whether a note (or a release's reason) says the function matches already."""
    return any(not _NEGATED.search(text[max(0, m.start() - 40):m.start()]) for m in _SAYS_MATCHED.finditer(text))


# A note that says little more than "already matches": on a function that doesn't, the brief leaves it out. Agents
# released 12 big open functions of a DS game, untried, on such notes ("Already 100% match on claim, no work needed"
# from a false 100% objdiff gave before #59), each one's note the next agent's reason.
ONLY_SAYS_MATCHED = 200


def stale_note(text: str, status: str) -> str:
    """A note as a brief shows it: one saying the function matches, on a function that doesn't, is marked out of
    date (the note itself stays as it was), so the brief's score is the number agents go by."""
    if status == "matched" or not says_matched(text):
        return text
    return ("[out of date: written when the function counted as matched; it doesn't match now, so go by the brief's "
            "current state] " + text)


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
    reached at `best_at`), is marked superseded and goes after the others, and so does a longer one saying a function
    that doesn't match matches (stale_note): on an Xbox game three of a function's six note slots held such notes
    while the finding that got it to 92% came after them. At most `show` (BRIEF_NOTES)."""
    show = BRIEF_NOTES if show is None else show
    best_score = fn["best_score"] if fn["status"] != "matched" else None
    best_percent = None
    if best_score is not None and fn["n_insns"]:
        best_percent = max(0.0, 100.0 * (1 - best_score / (fn["n_insns"] * 100)))
    infra, wrong, seen, fresh, stale = 0, 0, {}, [], []
    for n in rows:
        text = (n["text"] or "").strip()
        if _INFRA_NOTE.search(text):
            infra += 1
            continue
        if fn["status"] != "matched" and len(text) < ONLY_SAYS_MATCHED and says_matched(text):
            wrong += 1
            continue
        key = " ".join(text.split())
        if key in seen:
            seen[key][1] += 1
            continue
        item = [n, 1, False]
        seen[key] = item
        if fn["status"] != "matched" and says_matched(text):
            stale.append(item)  # stale_note marks it below
        elif (best_at is not None and best_score is not None and (n["created_at"] or 0) < best_at
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
    if wrong:
        lines.append(f"- ({wrong} note{'s' * (wrong != 1)} saying it already matches left out: it doesn't match, "
                     "and only a compile shows how close it is)")
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
    """Why a set-aside function is (SET_ASIDE: no C makes it, or it isn't the game's own code), for its page:
    {"set_aside": the reason, "not_c": whether agents agreed no C makes it}, or nothing."""
    meta = json.loads(fn["meta"] or "{}")
    if fn["status"] != "asm" and not meta.get("set_aside"):
        return {"boundary_review":meta["boundary_review"]} if meta.get("boundary_review") and fn["status"]!="matched" else {}
    why = meta.get("asm_code") or (f"the same code as {meta['asm_twin']}, a matched assembly function"
                                   if meta.get("asm_twin") else "hand-written assembly")
    return {"set_aside": why, "not_c": "not_c" in meta}

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def model_family(agent_name: str) -> str:
    """Who made an agent's model ("claude-opus-1": claude, "opencode-deepseek-v4.1-flash-2": deepseek); an agent
    whose name says no model is a family of its own."""
    kind, rest = agents.helper_parts(agent_name)
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


def move_on_brief(fn, after=None) -> str:
    """The brief's line naming the function's own move-on number (the one compile_and_diff counts against)."""
    custom = after is not None
    after = after if custom else stall_budget(fn["n_insns"] or 0)
    basis = "your worker setting" if custom else "this function’s size"
    return (f"move on after {after} compiles in a row without a better score ({basis}; "
            f"each compile_and_diff result shows the count). Recovery above the inherited best does not reset it. "
            'To leave earlier with a reason, use release_function(move_on=true, claim_token="the current claim_token", notes="what you tried and why").')


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


def claim_query(strategy: str) -> str:
    """The automatic claim's query for one order (STRATEGIES): open functions nobody holds, the release rules'
    exclusions and preferences first, then the order. Named arguments: Network._claim_plan."""
    # not what this agent released lately (see SELF_RELEASE_WINDOW); what it released before that last
    # functions waiting for another to match come last (WAITING); the ones they wait for, and those whose
    # wait is over, first (after what the release rules put back, before model routing's escalations)
    return (f"""SELECT f.* FROM functions f LEFT JOIN {_REGION} r ON r.region = f.vram / {REGION_BYTES}
            WHERE f.project_id=:pid AND f.status='open' AND (:weak = 0 OR f.abandons < :escalate)
            AND (:batch_unit IS NULL OR (f.unit=:batch_unit AND f.n_insns<=:batch_limit AND f.boost>=0
                 AND f.id NOT IN ({WAITING})))
            AND f.id NOT IN (SELECT value FROM json_each(:batch_sdk))
            AND (:below IS NULL OR f.best_score IS NULL OR
                 MAX(0.0, 100.0 * (1.0 - CAST(f.best_score AS REAL) / MAX(1, f.n_insns * 100))) < :below)
            AND (:tries IS NULL OR (SELECT COUNT(*) FROM attempts a
                 WHERE a.function_id=f.id AND a.agent_id=:agent) < :tries)
            AND ({int(strategy != 'no_compiling_c')} OR (f.best_score IS NULL AND NOT EXISTS (
                 SELECT 1 FROM attempts compiled_attempt WHERE compiled_attempt.function_id=f.id AND compiled_attempt.compiled=1)))
            AND f.id NOT IN (SELECT function_id FROM claims)
            AND f.id NOT IN (SELECT value FROM json_each(:stalled))
            AND f.id NOT IN (SELECT value FROM json_each(:model_tried))
            AND (f.id IN (SELECT value FROM json_each(:retry_grants)) OR f.name NOT IN (SELECT detail FROM events WHERE project_id=:pid AND agent_id=:agent
                               AND kind='release' AND created_at > :mine))
            ORDER BY CASE WHEN :custom THEN 0 ELSE f.id IN (SELECT value FROM json_each(:sdk)) END,
                     CASE WHEN :custom THEN 0 ELSE f.id IN ({WAITING}) END,
                     CASE WHEN :custom THEN 0 ELSE f.name IN (SELECT detail FROM events WHERE project_id=:pid AND agent_id=:agent
                                AND kind='release') END,
                     CASE WHEN :custom THEN 0 ELSE f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                AND agent_id IN (SELECT value FROM json_each(:kin)) AND created_at >
                                COALESCE((SELECT MAX(created_at) FROM events WHERE project_id=:pid
                                          AND kind IN ('context', 'compiler')), 0)) END,
                     CASE WHEN :custom THEN 0 ELSE f.name IN (SELECT detail FROM events WHERE project_id=:pid AND kind='release'
                                AND created_at > :cooled) END,
                     CASE WHEN :custom THEN 0 ELSE f.id IN ({READY}) END DESC, CASE WHEN :custom THEN 0 ELSE f.id IN ({WANTED}) END DESC,
                     CASE WHEN :custom THEN 0 ELSE f.id IN (SELECT value FROM json_each(:machine_pending)) END,
                     :strong AND f.abandons >= :escalate DESC, {STRATEGIES[strategy]} LIMIT 1""")


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


class Network:
    def __init__(self, db_path: str | Path, claim_ttl: int = CLAIM_TTL, *, repair_archive=None, project_databases=False):
        self._project_files = None
        self._function_owners: dict[int, int] = {}  # function ID -> its project (function_store)
        self.store = Store(db_path,repair_archive=repair_archive)
        try:
            from .project_migration import state, upgrade
            if project_databases or state(self.store):
                upgrade(self)
                from .project_files import Files
                self._project_files = Files(self)
            from types import SimpleNamespace

            from . import store as storage
            from .permuter_claims import clear, nonexclusive
            if not storage.ATTACH_ONLY:  # the app's own leases: a reader process leaves them to it
                identities = [r['id'] for r in self.store.all('SELECT id,name,owner FROM agents') if nonexclusive(SimpleNamespace(**dict(r)))]
                for selected,_ in self.project_stores():
                    with selected.tx() as db:clear(db,agent_ids=identities)
        except BaseException:
            if self._project_files is not None:
                for store in self._project_files.opened.values():store.conn.close()
            self.store.conn.close()
            raise
        self.claim_ttl = claim_ttl
        self._compile_ahead_local = threading.local()
        self._attempt_locks: dict[tuple[int, int], threading.RLock] = {}
        from .app.purpose_work import Groups
        self._pool = Groups(8)
        # (agent id, function id) -> its last compile's diff rows and their legend, for last_diff
        from .repair_feedback import RepairFeedback
        self.repair_feedback = RepairFeedback(self)
        self._last_diffs: dict[tuple[int, int], tuple[list, str]] = {}
        self._generation: dict[int, int] = {}  # project id -> changes compiles depend on (bump_generation)
        self._rename_generation: dict[int, int] = {}  # how many of those bumps were renames (polishing's guard)
        self._context_rejections = OrderedDict()
        self._context_trial_locks = {}
        self._compiled: OrderedDict[str, dict] = OrderedDict()  # compile_key -> result, for repeat_of

    def project_store(self, project: int | str) -> Store:
        """Select one operational file by catalog ID or slug, with no fallback.

        App startup migrates before workers begin. Library callers without an
        upgrade marker retain the legacy layout until explicitly enabled. Keep
        this Store's transaction, read cache and callbacks for the whole action.
        """
        if not ((type(project) is int and project > 0) or
                (isinstance(project, str) and project.strip())):
            raise ValueError("Project storage needs a positive catalog ID or a nonempty slug.")
        return self._project_files.get(project) if self._project_files is not None else self.store

    @contextmanager
    def project_registration(self, *, slug, name, description, arch, big_endian, root, config, existing=None):
        """Register initial rows on their owner and publish only a committed file.

        Reimports select the existing owner. Legacy library callers retain their
        original one-file atomic transaction until app startup upgrades them.
        """
        if existing is None and self._project_files is not None:
            values = (slug,name,description,arch,int(big_endian),str(root),json.dumps(config))
            with self._project_files.create(values) as result:yield result
            return
        store = self.store if existing is None else self.project_store(existing)
        with store.tx() as db:
            pid = existing
            if pid is not None and not db.execute('SELECT 1 FROM projects WHERE id=? AND slug=?',(pid,slug)).fetchone():
                raise NetworkError('The project registration does not match its existing project.')
            if pid is None:
                pid = db.execute('INSERT INTO projects(slug,name,description,arch,big_endian,root,config) '
                                 'VALUES(?,?,?,?,?,?,?)',
                                 (slug,name,description,arch,int(big_endian),str(root),json.dumps(config))).lastrowid
            yield db,pid

    def project_stores(self):
        """Discover operational owners explicitly; shared-layout callers still query once."""
        groups = {}
        for row in self.store.all('SELECT id FROM projects ORDER BY id'):
            groups.setdefault(self.project_store(row['id']), []).append(row['id'])
        return list(groups.items()) or [(self.store, [])]

    def project_rows(self):
        """Discover identities in the catalog, then read current paths/configuration."""
        for row in self.store.all('SELECT * FROM projects ORDER BY id'):
            store = self.project_store(row['id'])
            yield row if store is self.store else store.one('SELECT * FROM projects WHERE id=?',row['id'])

    def function_store(self, function_id):
        """Resolve old ID-only provenance through indexed owner discovery, never SQL routing.

        A project database allocates its rows in its catalog ID's range (shard_ids.BLOCK), so a function ID from
        that range is looked up in that one database first, and an ID found by a search of every database is
        remembered: each lookup opened and read all 74 project databases of a big library. Both are checked by
        the row itself before use, so a removed function or project searches again."""
        if self._project_files is not None:
            from .shard_ids import BLOCK
            for pid in dict.fromkeys(p for p in (self._function_owners.get(function_id), function_id // BLOCK) if p):
                try:
                    store = self.project_store(pid)
                except (NetworkError, ValueError):  # not (or no longer) a project in the catalog
                    continue
                if store.one('SELECT 1 FROM functions WHERE id=?', function_id):
                    return store
        found = None
        for store, ids in self.project_stores():
            if store.one('SELECT 1 FROM functions WHERE id=?', function_id):
                if found is not None:raise NetworkError('A function ID exists in several project databases. Restore a consistent backup.')
                found = store
                if store is not self.store and ids:
                    self._function_owners[function_id] = ids[0]
        return found

    # ------------------------------------------------------------------ agents

    def register_agent(self, name: str, owner: str | None = None, *, trusted: bool = True, origin: str = "cli") -> str:
        """Mint an agent token for a managed helper or manual MCP connection (`trusted` stays on the record for older databases)."""
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
        token_hash = hash_token(token)
        row = self.store.one("SELECT id,name,owner,trusted,last_seen FROM agents WHERE token_hash=?", token_hash)
        if row is None:
            raise NetworkError("invalid API token")
        now = time.time()
        # Presence is advisory (the room uses a 30-second online window), not an
        # authentication requirement. Polls must not queue behind compiler writes.
        if row["last_seen"] is None or not 0 <= now - row["last_seen"] < 5:
            with self.store.try_tx() as db:
                if db is not None:
                    db.execute("UPDATE agents SET last_seen=? WHERE id=? AND token_hash=? "
                               "AND (last_seen IS NULL OR last_seen<=? OR last_seen>?)",
                               (now, row["id"], token_hash, now-5, now))
        return Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))

    # ---------------------------------------------------------------- pairing (an outside agent asks; the user approves)

    PAIR_SECRET_CHARS = 16  # the requester's own secret, so only it can read the answer

    def pair_request(self, name: str, secret: str) -> str:
        """An agent outside the app asks to work one of its projects: a request the user sees and approves (or not)
        in the window. The secret stays the requester's: without it, the answer (the token) can't be read."""
        name = name.strip()
        if not name or len(name) > 40 or not all(c.isalnum() or c in "-_." for c in name):
            raise NetworkError("agent name must be 1-40 chars of [A-Za-z0-9-_.]")
        if not isinstance(secret, str) or len(secret) < self.PAIR_SECRET_CHARS:
            raise NetworkError(f"secret must be at least {self.PAIR_SECRET_CHARS} characters")
        with self.store.tx() as db:
            pending = db.execute("SELECT COUNT(*) FROM pairings WHERE state='pending'").fetchone()[0]
            if pending >= 10:
                raise NetworkError("too many pending pairing requests: decide them in the window first")
            id = uuid.uuid4().hex
            db.execute("INSERT INTO pairings(id,name,secret_hash,created_at) VALUES(?,?,?,?)",
                       (id, name, hash_token(secret), time.time()))
        return id

    def pairings(self) -> list[dict]:
        """The requests waiting for the user's decision."""
        return [dict(r) for r in self.store.all("SELECT id,name,created_at FROM pairings WHERE state='pending'")]

    def pair_decide(self, id: str, approve: bool, project: str | None) -> dict:
        """Approve (with the one project the agent may work) or refuse a pairing. Approving mints the agent now; the
        token reaches the requester when it asks again with its secret."""
        if approve and (not project or self.store.one("SELECT 1 FROM projects WHERE slug=?", project) is None):
            raise NetworkError(f"no project called {project!r}")
        with self.store.tx() as db:
            row = db.execute("SELECT name,state FROM pairings WHERE id=?", (id,)).fetchone()
            if row is None or row["state"] != "pending":
                raise NetworkError("no such pending pairing")
            if approve:
                token = self.register_agent(row["name"], "you", origin="paired")
                agent_id = self.store.one("SELECT id FROM agents WHERE token_hash=?", hash_token(token))["id"]
                db.execute("UPDATE pairings SET state='approved',agent_id=?,project=?,token=?,decided_at=? WHERE id=?",
                           (agent_id, project, token, time.time(), id))
            else:
                db.execute("UPDATE pairings SET state='denied',decided_at=? WHERE id=?", (time.time(), id))
        return {"state": "approved" if approve else "denied"}

    def pair_answer(self, id: str, secret: str) -> dict:
        """What the requester sees: its request's state, with the token once the user approved it."""
        row = self.store.one("SELECT secret_hash,token,state FROM pairings WHERE id=?", id)
        if row is None or not secrets.compare_digest(row["secret_hash"], hash_token(secret)):
            raise NetworkError("no such pairing request")
        out = {"state": row["state"]}
        if row["state"] == "approved":
            out["token"] = row["token"]
        return out

    def pairing_project(self, agent_id: int) -> str | None:
        """The project a paired agent was approved for (the app's own helpers are mapped in memory instead)."""
        row = self.store.one("SELECT project FROM pairings WHERE agent_id=? AND state='approved'", agent_id)
        return row["project"] if row else None

    # ---------------------------------------------------------------- helpers

    def _project(self, slug: str | None):
        if slug:
            row = self.project_store(slug).one("SELECT * FROM projects WHERE slug=?", slug)
            if row is None:
                raise NetworkError(f"no project {slug!r} here: check its name, or whether it was removed")
            return row
        rows = self.store.all("SELECT * FROM projects")
        if len(rows) == 1:
            row = rows[0]
            store = self.project_store(row["id"])
            selected = row if store is self.store else store.one("SELECT * FROM projects WHERE id=?", row["id"])
            if selected is None:
                raise NetworkError(f"Project {row['slug']!r} is missing from its database. Restore it from backup.")
            return selected
        raise NetworkError("several projects exist; pass `project` (its slug)")

    @staticmethod
    def _function_reference(name: str, project: str | None):
        """Claim labels include the project; @unit paths are part of a stored function name."""
        prefix, separator, local = name.partition("/")
        if separator and "@" not in prefix and (project is None or prefix == project):
            return local, prefix
        return name, project

    def _function(self, name: str, project: str | None):
        local, scope = self._function_reference(name, project)
        if project is None:
            name, project = local, scope
        if project is None:
            rows = []
            for store, ids in self.project_stores():
                if store is self.store:
                    rows.extend(store.all('SELECT * FROM functions WHERE name=? LIMIT 2', name))
                else:
                    rows.extend(store.all('SELECT * FROM functions WHERE name=? AND project_id IN '
                                          '(SELECT value FROM json_each(?)) LIMIT 2', name, json.dumps(ids)))
                if len(rows) > 1:break
            if len(rows) > 1:
                raise NetworkError(f"{name!r} exists in several projects; pass `project`")
            row = rows[0] if rows else None
        else:
            store = self.project_store(project)
            row = store.one(
                "SELECT f.* FROM functions f JOIN projects p ON p.id=f.project_id WHERE p.slug=? AND f.name=?",
                project, name,
            )
            # Preserve exact stored names before interpreting a copied project/name label.
            if row is None and local != name:
                row = store.one(
                    "SELECT f.* FROM functions f JOIN projects p ON p.id=f.project_id WHERE p.slug=? AND f.name=?",
                    project, local,
                )
        if row is None:
            raise NetworkError(f"no function {name!r}" + (f" in {project!r}" if project else ""))
        return row

    def _context(self, project_id: int, unit: str | None = ""):
        """Latest header for a translation unit, falling back to the project-wide one. Real codebases have
        one header set per source file; small projects just have the project-wide header ('' unit)."""
        store = self.project_store(project_id)
        unit = unit or ""
        def latest():
            row = store.one("SELECT * FROM context_versions WHERE project_id=? AND unit=? "
                                 "ORDER BY version DESC LIMIT 1", project_id, unit)
            if row is None and unit:
                row = store.one("SELECT * FROM context_versions WHERE project_id=? AND unit='' "
                                     "ORDER BY version DESC LIMIT 1", project_id)
            return row
        return store.read_cache.get(('latest-context',project_id,unit),
                                         store.read_version('scope_context',project_id),latest)

    def layout_symbols(self, pid: int) -> list[dict]:
        """The names given in a project, for a room's state and a save. A name one of several functions at an
        address holds (overlays share addresses) also says that function's unit, so the other side names the same
        one (#107 review); elsewhere the list is as it always was."""
        from .rom.symbols import AUTO_NAME
        store = self.project_store(pid)
        from .metadata_rows import rows as metadata_rows
        symbols = metadata_rows(store, ('name','address','size','kind'),
                                'SELECT name,address,size,kind FROM symbols WHERE project_id=?', pid)
        out = [row for row in symbols if not AUTO_NAME.match(row['name'])]
        if not out:
            return out
        # Only exported names need overlay units. Auto-generated overlay names
        # previously crossed the Python boundary just to be discarded afterwards.
        twins = {r["name"]: r["unit"] for r in store.all(
            "SELECT name,unit FROM functions WHERE project_id=? AND name IN "
            "(SELECT value FROM json_each(?)) AND vram IN "
            "(SELECT vram FROM functions WHERE project_id=? GROUP BY vram HAVING COUNT(*) > 1)",
            pid, json.dumps([row['name'] for row in out]), pid)}
        for row in out:
            if row['name'] in twins:
                row['unit'] = twins[row['name']]
        return out

    def _contexts(self, project_id: int, units):
        """Latest requested headers in one snapshot, including common-header fallback.

        Bulk exports must not cycle hundreds of units through the bounded per-unit
        read cache. Select version metadata first; only those versions are decoded
        and pinned through the ordinary header view. No historical content scan.
        """
        units = {unit or "" for unit in units}
        if not units:return {}
        store = self.project_store(project_id)
        rows = store.all("""WITH requested AS MATERIALIZED (
            SELECT value AS unit FROM json_each(?)),
            latest AS MATERIALIZED (
                SELECT unit,MAX(version) AS version FROM context_history
                WHERE project_id=? AND (unit='' OR unit IN (SELECT unit FROM requested)) GROUP BY unit),
            selected AS (
                SELECT DISTINCT COALESCE(l.version,(SELECT version FROM latest WHERE unit='')) AS version
                FROM requested r LEFT JOIN latest l ON l.unit=r.unit)
            SELECT c.* FROM selected s JOIN context_versions c
            ON c.project_id=? AND c.version=s.version""",
            json.dumps(sorted(units)), project_id, project_id)
        current = {row['unit']:row for row in rows}
        return {unit:current.get(unit,current.get("")) for unit in units}

    def _unit_contexts(self, project_id: int) -> set[str]:
        store = self.project_store(project_id)
        return {r["unit"] for r in store.all(
            "SELECT DISTINCT unit FROM context_versions WHERE project_id=? AND unit != ''", project_id)}

    def _effective_unit(self, fn, units: set[str]) -> str:
        return fn["unit"] if fn["unit"] in units else ""

    def _target_obj(self, fn, proj) -> FunctionObj:
        return FunctionObj(
            name=fn["name"], arch=proj["arch"], big_endian=bool(proj["big_endian"]), code=fn["target"],
            relocs=[Reloc.from_json(r) for r in json.loads(fn["relocs"])],
        )

    def _machine_agent_ids(self):
        """Shared identity IDs, resolved before a separate project transaction."""
        return {row['id'] for row in self.store.all('SELECT id FROM agents WHERE name IN (?,?)', *MACHINE_AGENTS)}

    def _nonexclusive_agent_ids(self):
        """Resolve legacy speculative leases from the shared identity registry."""
        from types import SimpleNamespace

        from .permuter_claims import nonexclusive
        def load():
            return frozenset(row['id'] for row in self.store.all(
                "SELECT id,name,owner FROM agents WHERE name LIKE '%permuter%'")
                    if nonexclusive(SimpleNamespace(**dict(row))))
        # Historical permuter workers accumulate even when no leases remain.
        # Cache only derived IDs, never authentication, with transactional invalidation.
        return set(self.store.read_cache.get('nonexclusive-agent-ids',
                                             self.store.read_version('lease_identities'),load))

    def _expire_claims(self, db, *, machine_ids=None, nonexclusive_ids=None) -> None:
        from .permuter_claims import clear
        clear(db, agent_ids=nonexclusive_ids)
        now = time.time()
        own = (machine_ids if machine_ids is not None else
               {r["id"] for r in db.execute("SELECT id FROM agents WHERE name IN (?, ?)", MACHINE_AGENTS)})
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
            if kind == "rename":
                self._rename_generation[project_id] = self._rename_generation.get(project_id, 0) + 1

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
        if hit.get('diff'):
            rows, legend = hit['diff']
            self.keep_diff(agent.id, fn['id'], rows, legend)
        who = "your" if hit["agent_id"] == agent.id else f"{self._agent_name(hit['agent_id'])}'s"
        head = (f"🔁 REPEAT: this source is the same (indentation aside) as {who} compile #{hit['n']} of {fn['name']} "
                f"({_ago(hit['at'])}), and nothing it depends on changed since, so it wasn't built again and doesn't "
                "count toward the move-on number. That compile's result:\n")
        return {"text": head + hit["text"] + self.stall_note(fn["id"], agent.id, project_id=fn["project_id"]), "score": hit["score"],
                "matched": False, "exact": False, "new_match": False, "adopted": False, "repeat": True}

    def remember_compile(self, key: str, agent: Agent, fn, text: str, score: int | None) -> None:
        """A compile's result (without its move-on line), for repeat_of."""
        store = self.project_store(fn["project_id"])
        n = store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND agent_id=?", fn["id"],
                           agent.id)["n"]
        self._compiled[key] = {"text": text, "score": score, "agent_id": agent.id, "n": n, "at": time.time(),
                               "diff": self._last_diffs.get((agent.id, fn["id"])) if score is not None else None}
        while len(self._compiled) > REPEATS_KEPT:
            self._compiled.popitem(last=False)

    def same_score_note(self, fn_id: int, agent_id: int, score: int | None, source: str, *, project_id=None) -> str:
        """When a new variant only reproduces a score an earlier, different source of this claim had: which compile
        it equals (in one project, 21 of 56 compiles in two long sessions did, 1001 seven times on one function)."""
        store = self.project_store(project_id) if project_id is not None else self.store
        st = self.stall_status(fn_id, agent_id, project_id=project_id)
        if score is None or st["claimed_at"] is None:
            return ""
        rows = store.all("SELECT score, source FROM attempts WHERE function_id=? AND agent_id=? AND created_at >= ? "
                              "ORDER BY id", fn_id, agent_id, st["claimed_at"])
        mine = normalized_source(source)
        for k, r in enumerate(rows[:-1], 1):  # (the last row is this compile)
            if r["score"] == score and normalized_source(r["source"] or "") != mine:
                return (f"\nSame score as your compile #{k} of this claim ({score}) with a different source: this "
                        "score alone does not prove identical assembly. Check the remaining differences before repeating this kind of change.")
        return ""

    def _agent_name(self, agent_id) -> str:
        row = self.store.one("SELECT name FROM agents WHERE id=?", agent_id) if agent_id else None
        return row["name"] if row else "?"

    @timed_build
    def _compile(self, proj, header: str, source: str, fn=None):
        """Compiled with the project's compiler, or for `fn` the one its address range is built with."""
        cfg = json.loads(proj["config"])
        if fn is not None and (cfg.get("compiler_ranges") or cfg.get('function_compilers')):
            cfg = {**cfg, "compiler": compiler_for(cfg, fn["vram"], fn)}
        from . import compile_configuration, compile_timing
        with compile_timing.phase('configuration'):
            invocation = compile_configuration.resolve(Path(proj['root']),cfg,compiler_config)
        identity = dict(fn) if fn is not None else {}
        c = cfg['compiler']
        if 'toolchain' in c and not c.get('ro_paths') and not any(
                flag.startswith(('-I','-include','-imacros','/I','/FI')) for flag in c.get('flags',[])):
            invocation.identity = (proj['id'], proj['root'], proj['config'],
                                   *(identity.get(k) for k in ('id','name','vram','meta','target','relocs')))
        try:
            result = compile_source(invocation, header, source)
        except OSError as error:
            from .storage_errors import message
            if reason := message(error):
                # Host resource failures are retryable; they are not failed candidate C.
                raise NetworkError(reason) from error
            raise
        result.effective_settings = {'compiler':cfg['compiler'], 'effective_command':list(invocation.cmd),
                                     'language':getattr(result,'language','unknown'), 'settings_source':'compile invocation snapshot'}
        return result

    @staticmethod
    def _compile_snapshot(proj, fn, header, source):
        # Include naming metadata: canonical_source can change the compiler input after a rename.
        return (proj["id"], proj["root"], proj["config"], fn["id"], fn["name"], fn["vram"],
                fn["meta"], header, source)

    @contextmanager
    def compiled_ahead(self, prepared, *, strict=False):
        """Offer one background compile to the ordinary attempt/judge on this thread only.

        A changed compiler, header, source or function identity falls back to a fresh compile.
        Strict saved verification instead requests a compile outside its verdict lock
        and retries the ordinary attempt against current inputs before recording.
        No claims, judging or result recording may run in the compile worker.
        """
        previous = getattr(self._compile_ahead_local, "prepared", None)
        prior_strict = getattr(self._compile_ahead_local, "strict", False)
        self._compile_ahead_local.prepared = prepared
        self._compile_ahead_local.strict = strict
        try:
            yield
        finally:
            self._compile_ahead_local.prepared = previous
            self._compile_ahead_local.strict = prior_strict

    def _attempt_compilation(self, proj, header, source, fn):
        prepared = getattr(self._compile_ahead_local, "prepared", None)
        if prepared is not None:
            snapshot, future = prepared
            if snapshot == self._compile_snapshot(proj, fn, header, source):
                return future.result()
        if getattr(self._compile_ahead_local, "strict", False):
            from .saved_compilation import CompileAgain
            raise CompileAgain(proj, header, source, fn, self._compile_snapshot(proj, fn, header, source))
        return self._compile(proj, header, source, fn)

    def _candidate_checks(self, fn, proj, obj: bytes) -> dict:
        """Advisory diagnostics supplied by ROM engines; imported builds keep their whole-image gate."""
        return {}

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
            proj = self.project_store(fn["project_id"]).one("SELECT * FROM projects WHERE id=?", fn["project_id"])
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
        still = self.still_holding(agent, function, project=project)
        fn = self.claim_row(agent, project, function, strategy)
        if fn is None:
            return self.nothing_to_claim(agent, self._project(project))
        return still + self._brief(fn, agent, header="CLAIMED")

    def still_holding(self, agent: Agent, function: str | None, *, project=None) -> str:
        """What to say first when claim_function (no function named) gives an agent the one it already holds: after
        a pushed-back release it still does, and an agent read the same brief coming back as the queue handing it
        back the function it had just released."""
        if function:
            return ""
        stores = [(self.project_store(project), [])] if project else self.project_stores()
        held = None
        for store, _ in stores:
            held = store.one("SELECT f.name,c.claimed_at FROM claims c JOIN functions f ON f.id=c.function_id "
                             "WHERE c.agent_id=?", agent.id)
            if held is not None:
                if store.one("SELECT 1 FROM events WHERE agent_id=? AND kind='release_pushed' AND detail=? "
                             "AND created_at>=?", agent.id, held['name'], held['claimed_at'] or 0):break
                return ''
        if held is None:return ''
        return (f"You still hold {held['name']}: it wasn't released (release_function was pushed back), so this is "
                "your claim on it, not a new one. Compile the next step; release_function goes through once you've "
                "compiled it, when you ask again.\n\n")

    def _own_release_cutoff(self, store, agent_id: int, now: float, proj=None) -> float:
        """Releases by this agent after this time keep it off those functions: the last SELF_RELEASE_WINDOW, and
        only since its run started (its last 'freed' event, release_stopped's, or 'stopped', a helper's own).
        Two seeks in idx_events_agent_kind_time, not a walk over every agent's 'freed' events."""
        row = store.one("SELECT MAX(created_at) AS t FROM events WHERE agent_id=? AND kind IN ('freed', 'stopped')",
                        agent_id)
        from .pause_scope import cooldown_start
        worker=self.store.one('SELECT id,name FROM agents WHERE id=?',agent_id)
        boundary=cooldown_start(self,proj,worker) if proj is not None and worker else 0
        return max(now - SELF_RELEASE_WINDOW, (row["t"] if row else None) or 0, boundary)

    def nothing_to_claim(self, agent: Agent, proj, scope: str = "") -> str:
        """What a claim that found nothing tells the agent: whether the open functions are held (by whom, for how
        long), ones it set aside itself (which it can still take by name), or really all done. A bare "nothing left"
        sent four helpers away from a project with ten open functions."""
        now = time.time()
        n_open = self.project_store(proj['id']).one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='open'",
                                proj["id"])["n"]
        if not n_open:
            return f"Nothing left to claim in {proj['slug']}: every function{scope} matches. 🎉"
        held = self.project_store(proj['id']).all(
            "SELECT f.name,c.agent_id,c.expires_at FROM claims c JOIN functions f ON f.id=c.function_id "
            "WHERE f.project_id=? AND f.status='open' ORDER BY c.expires_at",
            proj["id"])
        from .app.project_labels import label
        held = label(self, held, {'agent':'agent_id'})
        cutoff = self._own_release_cutoff(self.project_store(proj['id']), agent.id, now, proj)
        mine = self.project_store(proj['id']).all(
            "SELECT DISTINCT f.name FROM functions f JOIN events e ON e.project_id=f.project_id AND e.detail=f.name "
            "WHERE f.project_id=? AND f.status='open' AND e.agent_id=? AND e.kind='release' AND e.created_at > ? "
            "AND f.id NOT IN (SELECT function_id FROM claims) ORDER BY f.name", proj["id"], agent.id, cutoff)
        policy = self.selection(proj["slug"], agent.name)
        selection_note = (f" Work selection: {policy.get('strategy') or 'automatic'}"
                          + (f", below {policy['match_below']:g}% match" if policy.get("match_below") is not None else "")
                          + (f", max {policy['max_tries']} tries per function" if policy.get("max_tries") else "")
                          + ". No eligible free function is available; do not repeatedly claim. Change the filter or wait."
                          if any(v is not None and v != "" for v in policy.values()) else "")
        lines = [f"Nothing to claim in {proj['slug']} right now: {n_open} function{'s' * (n_open != 1)} "
                 f"{'are' if n_open != 1 else 'is'} open, but none is free for you.{selection_note}"]
        from . import claim_stalls
        paused = claim_stalls.blocked(self,proj,agent)
        from .app.data_regions import blocked as data_blocked
        data_paused = data_blocked(self,proj)
        if data_paused:
            lines.append(f'{len(data_paused)} entries are paused for data-region review in Settings. Existing work is retained.')
        if paused:
            lines.append(f'{len(paused)} unchanged candidates are paused after repeated releases. Quality warnings do not block work. Exhausted candidates retry when '
                         'source, build context, symbols or repair rules change. Explicit claim_function(function=name) '
                         'can still select one; do not loop on automatic claims.')
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

    @staticmethod
    def validate_selection(policy: dict | None) -> dict:
        """User queue choices are enforced by the scheduler, not left to the model's prompt."""
        if policy is not None and not isinstance(policy, dict):
            raise NetworkError("Work selection must contain a strategy and optional match percentage")
        policy = dict(policy or {})
        strategy = policy.get("strategy", "")
        if strategy is None:
            strategy = ""
        if not isinstance(strategy, str) or (strategy and strategy not in STRATEGIES):
            raise NetworkError(f"strategy must be one of {', '.join(STRATEGIES)}")
        below = policy.get("match_below")
        if below is not None and below != "":
            try:
                below = float(below)
            except (ValueError, TypeError):
                raise NetworkError("Match percentage must be a number from 0 to 100") from None
            if not 0 <= below <= 100:
                raise NetworkError("Match percentage must be a number from 0 to 100")
        else:
            below = None
        result = {"strategy": strategy, "match_below": below}
        if "disabled_tools" in policy:
            from .app.tool_policy import validate
            result["disabled_tools"] = validate(policy["disabled_tools"])
        if 'assembly_metadata' in policy:
            if type(policy['assembly_metadata']) is not bool:
                raise NetworkError('Assembly byte and address columns must be true or false.')
            result['assembly_metadata'] = policy['assembly_metadata']
        if 'model_untried' in policy:
            if not isinstance(policy['model_untried'], bool):
                raise NetworkError('Never tried by model must be true or false.')
            result['model_untried'] = policy['model_untried']
        limit = policy.get("max_tries")
        if limit is not None and limit != "":
            try:
                value = float(limit)
            except (ValueError, TypeError):
                raise NetworkError("Max tries must be a whole number from 1 to 10000") from None
            if isinstance(limit, bool) or not 1 <= value <= 10000 or not value.is_integer():
                raise NetworkError("Max tries must be a whole number from 1 to 10000")
            result["max_tries"] = int(value)
        stall = policy.get("stall_compiles")
        if stall is not None and stall != "":
            try:
                value = float(stall)
            except (ValueError, TypeError):
                raise NetworkError("No-improvement compiles must be a whole number from 1 to 1000") from None
            if isinstance(stall, bool) or not 1 <= value <= 1000 or not value.is_integer():
                raise NetworkError("No-improvement compiles must be a whole number from 1 to 1000")
            result["stall_compiles"] = int(value)
        unchanged=policy.get('unchanged_compiles')
        if unchanged is not None and unchanged != '':
            try:
                value=float(unchanged)
            except (TypeError,ValueError):
                raise NetworkError('Unchanged-output compiles must be a whole number from 0 to 1000') from None
            if isinstance(unchanged,bool) or not 0<=value<=1000 or not value.is_integer():
                raise NetworkError('Unchanged-output compiles must be a whole number from 0 to 1000')
            result['unchanged_compiles']=int(value)
        return result

    def set_selection(self, project: str, agent: str, policy: dict | None) -> dict:
        policy = self.validate_selection(policy)
        with self.project_store(project).tx() as db:
            db.execute("INSERT OR REPLACE INTO agent_selection VALUES(?,?,?)", (project, agent, json.dumps(policy)))
        return policy

    def selection(self, project: str, agent: str) -> dict:
        row = self.project_store(project).one("SELECT policy FROM agent_selection WHERE project=? AND agent=?", project, agent)
        return json.loads(row["policy"]) if row else {}

    def agent_stall_budget(self, fn, agent=None, agent_id=None):
        """A worker override shared by briefs, compile feedback and release checks."""
        if agent is None and agent_id is not None:
            agent = self.store.one("SELECT name FROM agents WHERE id=?", agent_id)
        if agent is None:
            return None
        name = agent.name if isinstance(agent, Agent) else agent['name']
        project = self.project_store(fn['project_id']).one("SELECT slug FROM projects WHERE id=?", fn['project_id'])
        return self.selection(project['slug'], name).get('stall_compiles')

    def attempt_limit(self, agent: Agent, fn) -> tuple[int | None, int]:
        store = self.project_store(fn["project_id"])
        slug = store.one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"]
        limit = self.selection(slug, agent.name).get("max_tries")
        used = store.one("SELECT COUNT(*) AS n FROM attempts WHERE function_id=? AND agent_id=?",
                              fn["id"], agent.id)["n"] if limit is not None else 0
        return limit, used

    def focus_target(self,agent,project,function=None):
        from .app.focused_agents import target
        return target(self,agent,project,function)

    def ensure_agent_active(self, agent):
        if agent.id in getattr(self,'_agent_focus',{}):self.focus_target(agent,None)
        if agent.id in getattr(self, '_revoked_manual_agents', ()):
            raise NetworkError('This manual MCP connection was revoked. Create a new connection in the app.')

    @contextmanager
    def attempt_turn(self, agent: Agent, fn):
        # A CLI can submit parallel tool calls: serialize this worker's candidates so two submissions cannot
        # both spend the last allowed try. Never hold the database write lock while compiling.
        store = self.project_store(fn["project_id"])
        from .request_timing import timed_lock
        with timed_lock(store.lock,'write_lock_wait'):
            lock = self._attempt_locks.setdefault((agent.id, fn["id"]), threading.RLock())
        with timed_lock(lock,'candidate_lock_wait'):
            self.ensure_agent_active(agent)
            self.check_attempt_limit(agent, fn)
            yield

    def check_attempt_limit(self, agent: Agent, fn) -> None:
        """Candidate submissions (including compile errors) count across sessions, per worker and function."""
        store = self.project_store(fn["project_id"])
        if fn["status"] != "open":
            return  # polishing and reads of matched code have their own queue
        limit, used = self.attempt_limit(agent, fn)
        if limit is None or used < limit:
            return
        held = store.one("SELECT 1 FROM claims WHERE function_id=? AND agent_id=?", fn["id"], agent.id)
        if held:
            Network.release(self, agent, fn["name"], f"User's max tries reached ({used}/{limit}); best attempt retained.",
                            store.one("SELECT slug FROM projects WHERE id=?", fn["project_id"])["slug"])
        raise NetworkError(f"Max tries reached for {fn['name']} ({used}/{limit}). Your work is saved. "
                           "Call claim_function for another function, or ask the user to raise the limit.")

    def selection_counts(self, project: str, agent: Agent) -> dict:
        """One current policy/blocked scan for the runner; claims still recheck availability."""
        policy = self.selection(project, agent.name)
        proj = self._project(project)
        from .model_checks import tried
        excluded = tried(self, project, agent) if policy.get('model_untried') else set()
        from . import claim_stalls
        paused = claim_stalls.blocked(self, proj, agent)
        unavailable = set(paused) | self._selection_cooling(proj, agent)
        from .app.data_regions import blocked as data_blocked
        excluded.update(data_blocked(self, proj))
        row = self.project_store(project).one(
            "SELECT COUNT(*) selected, "
            "COALESCE(SUM(f.id NOT IN (SELECT value FROM json_each(?))),0) eligible, "
            "COALESCE(SUM(f.id NOT IN (SELECT value FROM json_each(?)) AND "
            "f.id NOT IN (SELECT function_id FROM claims WHERE expires_at>?)),0) available "
            "FROM functions f JOIN projects p ON p.id=f.project_id WHERE p.slug=? "
            "AND f.id NOT IN (SELECT value FROM json_each(?)) "
            "AND f.status='open' AND (? IS NULL OR f.best_score IS NULL OR "
            "MAX(0.0, 100.0 * (1.0 - CAST(f.best_score AS REAL) / MAX(1, f.n_insns * 100))) < ?) "
            "AND (? IS NULL OR (SELECT COUNT(*) FROM attempts a WHERE a.function_id=f.id AND a.agent_id=?) < ?)",
            json.dumps(list(paused)), json.dumps(list(unavailable)), time.time(), project, json.dumps(list(excluded)),
            policy.get('match_below'), policy.get('match_below'), policy.get('max_tries'), agent.id, policy.get('max_tries'))
        return dict(row)

    def _selection_cooling(self, proj, agent: Agent) -> set[int]:
        """The same worker's recent releases are not automatic work, even in a fresh session.

        Startup counts must honor the claim query's cooldown, or the app starts a
        model every thirty seconds just to discover that it cannot claim anything.
        Explicit retry grants and a recorded model switch retain their normal escape.
        """
        store = self.project_store(proj['id'])
        cutoff = self._own_release_cutoff(store, agent.id, time.time(), proj)
        cooling = {r['id'] for r in store.all(
            "SELECT id FROM main.functions WHERE project_id=? AND status='open' AND name IN "
            "(SELECT detail FROM events WHERE project_id=? AND agent_id=? AND kind='release' AND created_at>?)",
            proj['id'], proj['id'], agent.id, cutoff)}
        if cooling:
            from . import claim_stalls
            cooling.difference_update(claim_stalls.grants(self, proj, agent))
        return cooling

    def selection_remaining(self, project: str, agent: Agent, *, include_paused: bool = False, available_only: bool = False) -> int:
        policy = self.selection(project, agent.name)
        from .model_checks import tried
        excluded = tried(self, project, agent) if policy.get('model_untried') else set()
        from . import claim_stalls
        if not include_paused:
            excluded.update(claim_stalls.blocked(self,self._project(project),agent))
        from .app.data_regions import blocked as data_blocked
        excluded.update(data_blocked(self,self._project(project)))
        if available_only:
            excluded.update(self._selection_cooling(self._project(project), agent))
        return self.project_store(project).one(
            "SELECT COUNT(*) AS n FROM functions f JOIN projects p ON p.id=f.project_id WHERE p.slug=? "
            "AND f.id NOT IN (SELECT value FROM json_each(?)) "
            "AND (?=0 OR f.id NOT IN (SELECT function_id FROM claims WHERE expires_at>?)) "
            "AND f.status='open' AND (? IS NULL OR f.best_score IS NULL OR "
            "MAX(0.0, 100.0 * (1.0 - CAST(f.best_score AS REAL) / MAX(1, f.n_insns * 100))) < ?) "
            "AND (? IS NULL OR (SELECT COUNT(*) FROM attempts a WHERE a.function_id=f.id AND a.agent_id=?) < ?)",
            project, json.dumps(list(excluded)), available_only, time.time(), policy.get("match_below"), policy.get("match_below"), policy.get("max_tries"), agent.id,
            policy.get("max_tries"))["n"]

    def selection_wait_reason(self, project: str, agent: Agent, *, counts: dict | None = None) -> tuple[str, str]:
        """Explain the automatic-selection gate without implying its timer resets exhaustion."""
        total = self.project_store(project).one("SELECT COUNT(*) n FROM functions f JOIN projects p ON p.id=f.project_id "
                               "WHERE p.slug=? AND f.status='open'", project)["n"]
        counts = counts if counts is not None else self.selection_counts(project, agent)
        selected, eligible = counts['selected'], counts['eligible']
        paused = max(0, selected - eligible)
        filtered = max(0, total - selected)
        facts = [f"{paused} unchanged candidates paused in this worker's selection"] if paused else []
        from .app.boundary_repairs import blocked as boundary_blocked
        review_ids=boundary_blocked(self,self._project(project))
        review_count=self.project_store(project).one("SELECT COUNT(*) n FROM functions WHERE status='open' AND gone_mark IS NULL AND id IN (SELECT value FROM json_each(?))",json.dumps(list(review_ids)))['n']
        if review_count:facts.append(f"{review_count} incomplete entries waiting for boundary review")
        if filtered:
            facts.append(f"{filtered} open functions excluded by this worker's Work selection")
        if not facts:
            facts.append(f"{total} open functions; none currently available for automatic selection")
        held = self.project_store(project).one("SELECT COUNT(*) n FROM claims c JOIN functions f ON f.id=c.function_id "
                              "JOIN projects p ON p.id=f.project_id WHERE p.slug=? AND f.status='open' "
                              "AND c.expires_at>?", project, time.time())["n"]
        why = "; ".join(facts) + f". Project: {total} open functions, {held} currently claimed (counts can overlap)."
        todo = ("No AI request is running. Checks again every 30 seconds; this does not reset paused candidates. "
                "Candidate pauses are shared across agents using the same recorded model after repeated releases or reported exhaustion; other models can still try. "
                "Work resumes when source, build context, symbols, project instructions or repair rules change. "
                "Use Retry paused functions for a bounded fresh turn with this model, or claim an individual function explicitly by name. Change Work selection if functions are filtered out.")
        if review_count:todo += " Boundary entries require Settings → Code coverage → Review & repair boundaries. Keep an intentional entry explicitly to allow agent work."
        return why, todo

    def sdk_functions(self, proj) -> dict:
        """Recorded SDK identities; identification is separate from a verified C match."""
        store = self.project_store(proj["id"])
        return store.read_cache.get(('recorded-sdk', proj['id']), store.read_version('naming', proj['id']),
                                         lambda: self._recorded_sdk_functions(proj))

    def _recorded_sdk_functions(self, proj):
        store = self.project_store(proj["id"])
        out = {}
        for row in store.all("SELECT id,meta FROM functions WHERE project_id=? AND "
                                  "json_extract(meta, '$.naming.method')='sdk_signature'", proj["id"]):
            naming = json.loads(row["meta"])["naming"]
            if naming.get('binary_evidence'):
                continue  # Binary detections must still agree with the current target and corpus selection.
            out[row["id"]] = {"names": [naming.get("source_name") or naming["name"]],
                              "corpora": naming.get("source_corpora", []), "evidence": "recorded SDK signature"}
        return out

    def claim_row(self, agent: Agent, project: str | None = None, function: str | None = None,
                  strategy: str = "easiest", *, batch_from=None):
        """Claim (or renew) a function and return its row, or None when nothing is left."""
        proj = self._project(project) if (project or not function) else None
        if not function:  # the owner's order wins over the agent's, but a function asked for by name still comes
            order = json.loads(proj["config"]).get("claim_order") or ""
            strategy = order if order in CLAIM_ORDERS else strategy
        if strategy not in STRATEGIES:
            raise NetworkError(f"strategy must be one of {', '.join(STRATEGIES)}")
        proj = self._project(project) if (project or function is None) else None
        function = self.focus_target(agent,project,function)
        if batch_from is not None and agent.id in getattr(self,'_agent_focus',{}):return None
        sdk = self.sdk_functions(proj) if proj is not None and not function else {}
        from . import claim_stalls
        blocked = claim_stalls.blocked(self,proj,agent) if proj is not None and not function else []
        from .app.data_regions import blocked as data_blocked
        region_blocked = data_blocked(self,proj) if proj is not None else []
        blocked = list(set(blocked) | set(region_blocked))
        retry_grants = claim_stalls.grants(self,proj,agent) if proj is not None else []
        scope = proj["id"] if proj is not None else self._function(function, None)["project_id"]
        store = self.project_store(scope)
        now = time.time()
        from .app.local_claims import expiry_ids
        identities = expiry_ids(self, store)
        # The automatic pick reads the claim and release history; it reads it here, from a snapshot, not while
        # holding the project's writer: on a game with 48,000 claim events it took 60-900 ms, and every other
        # worker's claim, compile and release queued behind it. The transaction below re-checks the one function
        # picked (still open, still free) and picks again under the writer when it wasn't, so two workers never
        # hold one function.
        plan = candidate = None
        if not function and (batch_from is not None or
                             store.one("SELECT 1 FROM claims WHERE agent_id=?", agent.id) is None):
            plan = self._claim_plan(store, proj, agent, strategy, now, blocked=blocked,
                                    retry_grants=retry_grants, sdk=sdk, batch_from=batch_from)
            candidate = self._claim_pick(lambda sql, args: store._read(sql, args, single=True), *plan)
        with store.tx() as db:
            self.ensure_agent_active(agent)
            region_blocked = data_blocked(self,proj) if proj is not None else []
            blocked = list(set(blocked) | set(region_blocked))
            changes = db.total_changes
            self._expire_claims(db, **identities)
            expired = db.total_changes != changes
            held = db.execute("SELECT f.* FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?",
                              (agent.id,)).fetchone()
            if batch_from is not None:
                held = None
            elif held is not None and function:
                requested = self._function(function, proj['slug'] if proj else None)
                if db.execute('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',
                              (requested['id'],agent.id)).fetchone():
                    held = requested  # renew any task in an existing batch, not just its first row
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
                if fn["gone_mark"] is not None:
                    raise NetworkError("This entry is archived outside the detected build. Review or undo its boundary decision first.")
                if fn["status"] == "matched":
                    raise NetworkError(f"{fn['name']} is already matched; use get_function to read its source")
                if fn["status"] == "asm":
                    raise NetworkError(f"{fn['name']} is set aside ({set_aside(fn)['set_aside']}): claim another")
                slug = proj["slug"] if proj else db.execute("SELECT slug FROM projects WHERE id=?", (fn["project_id"],)).fetchone()["slug"]
                if fn['id'] in (region_blocked if proj is not None else data_blocked(self,db.execute('SELECT * FROM projects WHERE id=?',(fn['project_id'],)).fetchone())):
                    raise NetworkError('This entry needs boundary/data-region review. Review it in Settings and explicitly keep/resume it before claiming.')
                self.check_attempt_limit(agent, fn)
                selected = self.selection(slug, agent.name)
                if selected.get('model_untried'):
                    from .model_checks import tried
                    if fn['id'] in tried(self, slug, agent):
                        raise NetworkError('This function has already been checked by this model; claim another.')
                below = selected.get("match_below")
                if below is not None and fn["best_score"] is not None:
                    percent = max(0.0, 100.0 * (1.0 - fn["best_score"] / max(1, fn["n_insns"] * 100)))
                    if percent >= below:
                        raise NetworkError(f"{fn['name']} is outside this agent's match percentage filter; claim another")
                if (self.selection((proj or db.execute("SELECT slug FROM projects WHERE id=?", (fn["project_id"],)).fetchone())["slug"], agent.name).get("strategy") or strategy) == "no_compiling_c" and (
                        fn["best_score"] is not None or db.execute(
                            "SELECT 1 FROM attempts WHERE function_id=? AND compiled=1 LIMIT 1", (fn["id"],)).fetchone()):
                    raise NetworkError(f"{fn['name']} already has a recorded compiling C candidate; choose another function")
                other = db.execute("SELECT agent_id, expires_at FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
                if other is not None:
                    owner = self._agent_name(other['agent_id'])
                    if owner == 'you':
                        raise NetworkError(f"{fn['name']} is claimed by the user in the C editor. The user can select Let agents work on this; choose another eligible function meanwhile")
                    raise NetworkError(f"{fn['name']} is claimed by {owner} "
                                       f"for another {int(other['expires_at'] - now) // 60} min")
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
            else:
                if plan is None:  # its claim ran out (or a batch started) since the look before the transaction
                    plan = self._claim_plan(store, proj, agent, strategy, now, blocked=blocked,
                                            retry_grants=retry_grants, sdk=sdk, batch_from=batch_from)
                    candidate, expired = None, True
                plan[1]["stalled"] = json.dumps(blocked)
                fn = None
                if candidate is not None and candidate["id"] not in blocked:
                    fn = db.execute("SELECT * FROM functions WHERE id=? AND status='open' AND id NOT IN "
                                    "(SELECT function_id FROM claims)", (candidate["id"],)).fetchone()
                # the function picked outside was taken meanwhile, or leases ran out that may free a better one:
                # pick again under the writer, as every claim did before
                if fn is None and (candidate is not None or expired):
                    fn = self._claim_pick(lambda sql, args: db.execute(sql, args).fetchone(), *plan)
                if fn is None:
                    return None
                if fn['id'] in retry_grants and fn['id'] not in claim_stalls.grants(self,proj,agent):
                    return None  # a same-model worker consumed this grant before our claim transaction
                db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
            if proj is not None and fn['id'] in retry_grants:claim_stalls.consume(self,proj,fn,agent,db)
            # a wait that's over has had its turn at the front once it's claimed
            db.execute("DELETE FROM waits WHERE function_id=? AND needs_id IN (SELECT id FROM functions "
                       "WHERE status='matched')", (fn["id"],))
        return fn

    def _claim_plan(self, store, proj, agent: Agent, strategy: str, now: float, *, blocked, retry_grants, sdk,
                    batch_from):
        """The automatic claim's query and its named arguments, and the agent's model route. Only reads, through
        `store` (a snapshot outside the transaction, or the writer's own connection inside it): what it reads orders
        and filters the queue, and the claim transaction re-checks the function it takes."""
        policy = self.selection(proj["slug"], agent.name)
        from .model_checks import tried as model_tried
        excluded = model_tried(self, proj['slug'], agent) if policy.get('model_untried') else set()
        custom = bool(policy.get("strategy"))
        strategy = policy.get("strategy") or strategy
        # agents are registered in the catalog: a project database's own agents table is empty, and routing and
        # the kin preference read it there, so neither ran once projects had databases of their own
        names = {r["id"]: r["name"] for r in self.store.all("SELECT id, name FROM agents")}
        # Explicit user ordering wins over adaptive routing and release penalties. Leases and the
        # agent's recent-release exclusion still prevent two workers or a tight retry loop.
        route = None if custom else self._route(store, proj["id"], agent, now, names)
        family = model_family(agent.name)
        kin = [agent_id for agent_id, name in names.items() if model_family(name) == family]
        tried = store.one("SELECT SUM(status = 'matched') AS won, SUM(status = 'matched' OR attempts > 0) "
                          "AS tried FROM functions WHERE project_id=? AND status != 'asm'", proj["id"])
        overall = (tried["won"] or 0) / tried["tried"] if tried["tried"] else 0.5
        escalate = self._escalation(store, proj["id"]) if route else ESCALATE_AFTER
        args = {"retry_grants": json.dumps(retry_grants),
                "batch_sdk": json.dumps(batch_from.get("deferred", []) if batch_from is not None else []),
                "batch_unit": batch_from["unit"] if batch_from is not None else None,
                "batch_limit": batch_from["limit"] if batch_from is not None else None,
                "model_tried": json.dumps(list(excluded)), "stalled": json.dumps(blocked), "sdk": json.dumps(list(sdk)),
                "pid": proj["id"], "agent": agent.id, "kin": json.dumps(kin), "cooled": now - RELEASE_COOLDOWN,
                "overall": overall, "escalate": escalate, "weak": route == "weak", "strong": route == "strong",
                "mine": self._own_release_cutoff(store, agent.id, now, proj), "custom": custom,
                "below": policy.get("match_below"), "tries": policy.get("max_tries"),
                "machine_pending": json.dumps(self._machine_pending(proj, agent))}
        return claim_query(strategy), args, route

    def _machine_pending(self, proj, agent: Agent) -> list[int]:
        """The functions autopilot's running pass will still try (a machine draft through the judge, or a
        deterministic repair of saved C): the automatic claim takes them after everything else, so a model's tokens
        go to functions the free tries already had. Both queues go smallest first, so before this a helper started
        with a project's autopilot took the very functions autopilot was about to match. Only an order: a helper
        with nothing else left still gets one. A project's config `"machine_first": false` turns it off; an agent's
        own strategy (`custom`) skips it like the other preferences."""
        if agent.name in MACHINE_AGENTS or not json.loads(proj["config"] or "{}").get("machine_first", True):
            return []
        state = getattr(self, "autopilots", {}).get(proj["slug"]) or {}
        if state.get("state") != "running":
            return []
        pending = state.get("_pending")
        return sorted(pending.copy()) if pending else []  # (a copy in one step: autopilot's lanes take from it)

    @staticmethod
    def _claim_pick(read, query: str, args: dict, route: str | None):
        """The function the claim query puts first, through `read(sql, named_args)`; a weak model that finds only
        what stronger models are for takes it anyway: better tried than idle."""
        fn = read(query, args)
        if fn is None and route == "weak":
            fn = read(query, args | {"weak": False})
        return fn

    @staticmethod
    def _escalation(store, project_id: int) -> int:
        """How many give-ups make an open function the strongest model's (model routing): ESCALATE_AFTER, until every
        open function has had that many; then more than the median open function has, so a weak model keeps the
        less tried half. On an Xbox game whose 1,146 open functions had all been given up on 5 or more times, the
        weak model fell back to the whole queue: 3,183 claims and $138 for 10 matches in a night, while the strong
        model matched 27 of its 138."""
        given_up = sorted(r[0] for r in store.all("SELECT abandons FROM functions WHERE project_id=? AND "
                                                  "status='open'", project_id))
        if not given_up or given_up[0] < ESCALATE_AFTER:
            return ESCALATE_AFTER
        return given_up[(len(given_up) - 1) // 2] + 1

    def _route(self, store, project_id: int, agent: Agent, now: float, names: dict[int, str]) -> str | None:
        """"weak" for a model at work in the project well behind the strongest one at work, "strong" for that one
        while a weak one works too (see ESCALATE_AFTER), None otherwise: no routing until both are told apart by
        ROUTE_MIN_CLAIMS claims each. `names`: agent ID -> name, from the catalog."""
        claims, won, busy = collections.Counter(), collections.Counter(), {model_of(agent.name)}
        # idx_events_agent_kind_time answers this from the index, in agent order (no sort of every claim event)
        for r in store.all("SELECT agent_id, COUNT(*) AS n, MAX(created_at) AS last FROM events WHERE project_id=? "
                           "AND kind='claim' GROUP BY agent_id", project_id):
            if names.get(r["agent_id"], MACHINE_AGENTS[0]) not in MACHINE_AGENTS:
                claims[model_of(names[r["agent_id"]])] += r["n"]
                if r["last"] > now - ROUTE_ACTIVE_SECONDS:
                    busy.add(model_of(names[r["agent_id"]]))
        for r in store.all("SELECT matched_agent_id AS id, COUNT(*) AS n FROM functions WHERE project_id=? "
                           "AND status='matched' GROUP BY matched_agent_id", project_id):
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

    @release_operation("release")
    def release(self, agent: Agent, function: str, notes: str, project: str | None = None, not_c: bool = False, claim_token: str = "") -> str:
        """An agent gives a function back: its claim ends, its notes stay for the next agent, and the function
        counts one more give-up (the claim order puts those later). `not_c`: the agent found no C makes its code
        (NOT_C_AGENTS)."""
        fn = self._function(function, project)
        store = self.project_store(fn["project_id"])
        if not_c and not notes.strip():
            raise NetworkError("say in notes why no C makes it: the next agents and the user read it")
        progress = self.claim_progress(agent, fn)
        with store.tx() as db:
            row = db.execute("SELECT agent_id FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
            if row is None or row["agent_id"] != agent.id:
                raise NetworkError(f"you don't hold a claim on {fn['name']}")
            from .app import agent_review
            review_facts = (agent_review.facts(self,store.one('SELECT * FROM projects WHERE id=?',fn['project_id']),fn)
                            if agent_review.CONCERN.search(notes) or agent_review.load(self,agent_review.PREFIX+str(fn['id']),project_id=fn['project_id']) else None)
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            db.execute("UPDATE functions SET abandons=abandons+1 WHERE id=?", (fn["id"],))
            if notes.strip() and notes.strip() != "Deep permutation turn ended; improvements shared.":
                agent_notes.save(db, fn["project_id"], fn["id"], agent.id, notes.strip(), agent_name=agent.name)
            self._event(db, fn["project_id"], agent.id, "release", fn["name"])
            said = self._not_c(db, fn, agent, notes.strip()) if not_c else ""
        from . import claim_stalls
        claim_stalls.released(self,fn,agent=agent,exhausted=bool(progress and progress['compiles'] and progress['no_better'] >= progress['after']))
        from . import candidate_quality
        candidate_quality.capture(self,fn,agent,notes,progress)
        from .app import agent_review
        agent_review.observe(self,fn,agent,notes,progress,snapshot=review_facts)
        return (f"Released {fn['name']}. Your best attempt and notes are saved so the next agent starts "
                f"where you left off.{said} Call claim_function for a new one.")

    def _not_c(self, db, fn, agent: Agent, why: str) -> str:
        """An agent's verdict that no C makes fn's code; with NOT_C_AGENTS agents of NOT_C_MODELS models agreeing, fn
        is set aside (status asm). Returns what to tell the agent."""
        meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (fn["id"],)).fetchone()["meta"] or "{}")
        reader=getattr(self,'console',None)
        project=db.execute('SELECT * FROM projects WHERE id=?',(fn['project_id'],)).fetchone()
        boundary=reader(project).boundary_reason(fn['target'] or b'') if reader else None
        if meta.get('boundary_review') or boundary:
            return ' No assembly verdict recorded: review the incomplete target boundary first; the function remains available.'
        if (blocker := assembly_blocker(why)):
            meta['not_c'] = {name:vote for name,vote in meta.get('not_c',{}).items() if name != agent.name and not assembly_blocker(vote.get('why',''))}
            meta['assembly_review'] = {'reason':blocker, 'notes':why[:NOTE_CHARS], 'agent':agent.name, 't':time.time()}
            db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),fn['id']))
            return ' No assembly verdict recorded. '+blocker+' The function remains available; findings are saved in notes.'
        meta['not_c'] = {name:vote for name,vote in meta.get('not_c',{}).items() if not assembly_blocker(vote.get('why',''))}
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
        store = self.project_store(pid)
        said: dict[int, dict] = collections.defaultdict(dict)
        # notes from before pieces were joined to a function were about a piece, not about it (decompdecomp's note
        # on a join says so): they don't count
        joined = {r["function_id"]: r["t"] for r in store.all(
            "SELECT n.function_id, MAX(n.created_at) AS t FROM notes n WHERE "
            "n.project_id=? AND n.agent_id=? AND n.text LIKE '%joined%' GROUP BY n.function_id", pid,
            (self.store.one("SELECT id FROM agents WHERE name='decompdecomp'") or {'id':None})['id'])}
        from .app.project_labels import label
        rows = store.all("SELECT n.function_id,n.text,n.created_at,n.agent_id FROM notes n "
                         "JOIN functions f ON f.id=n.function_id WHERE f.project_id=? AND "
                         "f.status='open' AND (f.meta IS NULL OR f.meta NOT LIKE '%\"reopened\"%') ORDER BY n.id", pid)
        for r in label(self, rows, {'name':'agent_id'}, require=('name',)):
            if r["name"] in ("you", "decompdecomp", *MACHINE_AGENTS) or r["created_at"] <= joined.get(r["function_id"], float("-inf")) \
                    or not NOT_C_NOTE.search(r["text"]) or assembly_blocker(r["text"]):
                continue
            said[r["function_id"]][r["name"]] = {"model": model_of(r["name"]), "why": r["text"][:NOTE_CHARS],
                                                 "t": r["created_at"], "from": "note"}
        n = 0
        with store.tx() as db:
            for fid, verdicts in said.items():
                models = sorted({v["model"] for v in verdicts.values()})
                if len(verdicts) < NOT_C_AGENTS or len(models) < NOT_C_MODELS:
                    continue
                meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (fid,)).fetchone()["meta"] or "{}")
                if meta.get('boundary_review'):
                    continue
                why = max(verdicts.values(), key=lambda v: v["t"])["why"].split("\n")[0][:300]
                meta["not_c"] = {**{name:v for name,v in meta.get("not_c", {}).items() if not assembly_blocker(v.get("why", ""))}, **verdicts}
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
        store = self.project_store(fn["project_id"])
        if fn["status"] != "open":
            raise NetworkError(f"{fn['name']} isn't open (it's {fn['status']}): only an open function can be set aside")
        why = why.strip() or "no C makes it"
        meta = {k: v for k, v in json.loads(fn["meta"] or "{}").items() if k != "reopened"}
        meta["not_c"] = {**meta.get("not_c", {}), "you": {"model": "you", "why": why[:NOTE_CHARS], "t": time.time()}}
        meta["asm_code"] = f"you set it aside: {why}"
        with store.tx() as db:
            db.execute("DELETE FROM claims WHERE function_id=?", (fn["id"],))
            db.execute("UPDATE functions SET status='asm', meta=? WHERE id=? AND status='open'", (json.dumps(meta), fn["id"]))
            self._event(db, fn["project_id"], None, "not_c", f"{fn['name']}: set aside by you ({why[:120]})")

    def joined(self, function: str, project: str | None = None) -> None:
        """Pieces were joined to `function`: what set it aside (its shape, agents' verdicts) was about a piece of it,
        so it's open again, and the shape rules and notes written after the join (not_c_from_notes) decide anew. On a
        DS game an 8-byte piece agents agreed no C makes took seven more pieces, became a 276-byte function, and
        stayed set aside. Library code stays where the project put it (#52's set_aside)."""
        try:
            fn = self._function(function, project)
        except NetworkError:
            return
        meta = json.loads(fn["meta"] or "{}")
        if fn["status"] != "asm" or meta.get("set_aside"):
            return
        meta = {k: v for k, v in meta.items() if k not in ("asm_code", "asm_twin", "not_c")}
        with self.project_store(fn['project_id']).tx() as db:
            db.execute("UPDATE functions SET status='open', meta=? WHERE id=? AND status='asm'", (json.dumps(meta), fn["id"]))
            self._event(db, fn["project_id"], None, "reopen", f"{fn['name']}: open again (pieces were joined to it)")

    def reopen(self, function: str, project: str | None = None) -> None:
        """The user gives a set-aside function back to the agents (they think C makes it after all): the agents'
        verdicts on it are forgotten, and no check sets it aside again."""
        fn = self._function(function, project)
        store = self.project_store(fn["project_id"])
        meta = {k: v for k, v in json.loads(fn["meta"] or "{}").items() if k not in ("not_c", "asm_code", "asm_twin", "verified_assembly")}
        with store.tx() as db:
            db.execute("UPDATE functions SET status='open', meta=? WHERE id=? AND status='asm'",
                       (json.dumps({**meta, "reopened": True}), fn["id"]))
            self._event(db, fn["project_id"], None, "reopen", f"{fn['name']}: open again (you gave it back to the agents)")

    def release_stopped(self, agent_ids, why: str, *, project: str | None = None) -> list[str]:
        """The claims of agents whose process is gone, released at once with a note (`why`), instead of holding their
        functions until CLAIM_TTL runs out: after an update or a restart of the app, every open function looked held
        by helpers that had died with it. Not counted as giving up. Each function freed gets a 'freed' event (one
        without a function when the agent held none), which ends the agent's run: what it released before no longer
        keeps it off those functions (SELF_RELEASE_WINDOW). Returns the functions released."""
        now, freed = time.time(), []
        agent_ids = list(agent_ids)
        if not agent_ids:return freed
        own = self._machine_agent_ids()
        names = {r['id']:r['name'] for r in self.store.all(
            'SELECT id,name FROM agents WHERE id IN (SELECT value FROM json_each(?))', json.dumps(agent_ids))}
        # A single helper belongs to one project. Stopping it must not open or
        # write every idle project's operational database. Shutdown keeps the full sweep.
        if project is not None:
            proj = Network._project(self, project)
            stores = [(self.project_store(proj['id']), [proj['id']])]
        else:
            stores = self.project_stores()
        for store, projects in stores:
            with store.tx() as db:
                for aid in agent_ids:
                    scope = '' if project is None and store is self.store else ' AND f.project_id IN (SELECT value FROM json_each(?))'
                    args = (aid,) if project is None and store is self.store else (aid, json.dumps(projects))
                    rows = db.execute("SELECT f.id,f.project_id,f.name FROM claims c JOIN functions f ON f.id=c.function_id "
                                      "WHERE c.agent_id=?" + scope, args).fetchall()
                    for r in rows:
                        db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (r['id'], aid))
                        if aid not in own:
                            agent_notes.save(db, r['project_id'], r['id'], aid,
                                             f"Released because its agent stopped ({why}); its attempts are kept.",
                                             now, agent_name=names.get(aid))
                        self._event(db, r['project_id'], aid, 'freed', r['name'])
                        freed.append(r['name'])
                    if not rows:
                        # Each independent queue needs its own run boundary, even
                        # when this worker held nothing there at shutdown.
                        for pid in ([None] if project is None and store is self.store else projects):
                            self._event(db, pid, aid, 'freed', '')
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
        store = self.project_store(fn["project_id"])
        from .app import claim_state
        if header == "CLAIMED" and agent and claim_state.fast():
            return claim_state.brief(self,fn,agent,header)
        proj = store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        ctx = self._context(proj["id"], fn["unit"])
        claim = store.one("SELECT * FROM claims WHERE function_id=?", fn["id"])
        from . import candidate_quality
        lines = [f"## {header}: {proj['slug']}/{fn['name']}",
                 f"size {fn['size']} bytes, {fn['n_insns']} instructions, arch {proj['arch']}, "
                 f"context.h v{ctx['version']} ({len(ctx['header'].splitlines())} lines)"]
        cfg = json.loads(proj["config"])
        own = compiler_for(cfg, fn["vram"], fn)
        if own is not cfg.get("compiler"):  # a range built by another compiler than the rest of the game
            lines.append(f"compiled with {own['toolchain']} {' '.join(own['flags'])}: this address range's compiler, "
                         f"not the project's")
        if own.get('toolchain'):
            lines.append(f"compiler ID: {own['toolchain']}; compiler_experiments compiler=\"current\" retains it")
        if claim:
            from .release_receipts import token
            lines.append("claim_token: "+token(self,fn,claim))
            who = "you" if agent and claim["agent_id"] == agent.id else self._agent_name(claim["agent_id"])
            lines.append(f"claimed by {who}, lease expires in {int(claim['expires_at'] - time.time()) // 60} min "
                         f"(each compile_and_diff renews it)")
        if fn["status"] != "matched":
            lines.append(move_on_brief(fn, self.agent_stall_budget(fn, agent)))
        if fn["status"] == "matched":
            lines += [f"MATCHED by {self._agent_name(fn['matched_agent_id'])} {_ago(fn['matched_at'])}:",
                      "```c", fn["matched_source"].strip(), "```"]
        assembly = self._asm_text(fn, proj)
        compact = header == 'CLAIMED' and fn['best_source'] and fn['best_score'] is not None and 0 < fn['best_score'] <= max(1,fn['n_insns'])*5
        if compact and len(assembly.splitlines()) > 40:
            kept = self._last_diffs.get((agent.id,fn['id'])) if agent else None
            if kept:
                lines += ['', '### Saved mismatch evidence', scoring.render_rows(kept[0],context=2,max_rows=16)]
            assembly = '\n'.join(assembly.splitlines()[:40])
            lines += ['Near-match brief: first 40 target lines. Use get_assembly(start,limit) or get_function(section=full) for full evidence.']
        lines += ["", "### Target assembly", "```asm", scoring.compact_asm(assembly), "```"]
        if not compact:
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
        lines += candidate_quality.brief(self,fn)
        return "\n".join(lines)

    def notes_for_brief(self, fn, best_text: str = "", show: int | None = None, when: bool = True) -> list[str]:
        """A function's notes for its brief (brief_notes), with when its best score was first reached."""
        store = self.project_store(fn["project_id"])
        rows = store.all("SELECT n.text,n.created_at,n.agent_id FROM notes n "
                              f"WHERE n.function_id=? AND n.text != 'Deep permutation turn ended; improvements shared.' "
                              f"ORDER BY n.created_at DESC,n.id DESC LIMIT {BRIEF_NOTES * 8}", fn["id"])
        from .app.project_labels import label
        rows = label(self, rows, {'name':'agent_id'})
        best_at = None
        if fn["status"] != "matched" and fn["best_score"] is not None:
            row = store.one("SELECT MIN(created_at) AS t FROM attempts WHERE function_id=? AND score=?",
                                 fn["id"], fn["best_score"])
            best_at = row["t"] if row else None
        from .search_notes import useful
        return brief_notes(useful(rows), fn, best_at, best_text, show, when)

    def _next_step(self, proj, ctx) -> str:
        """The brief's last line for the agent that claimed the function."""
        return ("Next: read context.h with get_context, write C for this function, and iterate with "
                "compile_and_diff until score is 0. Submit only the function body (plus any helper "
                "declarations); context.h is included automatically.")

    # --------------------------------------------------------- compile & diff

    def attempt_limit_note(self, agent: Agent, function: str, project: str | None) -> str:
        fn = self._function(function, project)
        limit, used = self.attempt_limit(agent, fn)
        if fn["status"] == "open" and limit is not None and used >= limit:
            return (f"\nMax tries reached ({used}/{limit}); this function is released and your best attempt is saved. "
                    "Call claim_function for the next function.")
        return ""

    def compile_and_diff(self, agent: Agent, function: str, source: str, project: str | None = None) -> str:
        text = self.attempt(agent, function, source, project)["text"]
        return text + self.attempt_limit_note(agent, function, project)

    @trial
    def attempt(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        with self.attempt_turn(agent, self._function(function, project)):
            result = self._attempt_unlimited(agent, function, source, project)
            for key in ('exact','new_match','adopted'):result.setdefault(key,False)
            return result

    def _attempt_unlimited(self, agent: Agent, function: str, source: str, project: str | None = None) -> dict:
        """Compile, score and record one attempt. Returns {"text", "score", "matched"} (score None: didn't compile)."""
        fn = self._function(function, project)
        store = self.project_store(fn["project_id"])
        proj = store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        now = time.time()

        self.check_attempt_limit(agent, fn)

        # Claim bookkeeping first: you can't score (or get credit) on someone else's function.
        from .app.local_claims import expiry_ids
        identities = expiry_ids(self, store)
        with store.tx() as db:
            self._expire_claims(db, **identities)
            claim = db.execute("SELECT agent_id FROM claims WHERE function_id=?", (fn["id"],)).fetchone()
            scratch = fn["status"] == "matched"
            if not scratch:
                if claim is None:
                    held = db.execute("SELECT 1 FROM claims WHERE agent_id=?", (agent.id,)).fetchone()
                    if held:
                        from .app.claim_state import ownership_hint
                        raise NetworkError(ownership_hint(self,agent,fn["project_id"]))
                    db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + self.claim_ttl))
                    self._event(db, fn["project_id"], agent.id, "claim", fn["name"])
                elif claim["agent_id"] != agent.id:
                    owner = self._agent_name(claim['agent_id'])
                    hint = ('the user in the C editor. The user can select Let agents work on this to release it; choose another eligible function meanwhile.' if owner == 'you' else owner)
                    raise NetworkError(f"{fn['name']} is claimed by {hint}")
                else:
                    db.execute("UPDATE claims SET expires_at=? WHERE function_id=?", (now + self.claim_ttl, fn["id"]))

        ctx = self._context(proj["id"], fn["unit"])
        key = self.compile_key(fn, ctx["header"], str(ctx["version"]), proj["config"] or "", source)
        if not scratch and (again := self.repeat_of(key, agent, fn)) is not None:
            return again
        result = self._attempt_compilation(proj, ctx["header"], source, fn)

        if not result.ok:
            if re.search(r"Unknown section type|converter did not produce a big-endian ELF32|Missing [^\n]*reinstall", result.log, re.I):
                raise NetworkError("Toolchain infrastructure failure; this did not consume a repair attempt. Fix the compiler/converter setup before retrying.\n" + result.log.strip())
            self._record(fn, agent, None, source, ctx["version"], effective_settings=getattr(result,"effective_settings",None))
            text = f"❌ COMPILE ERROR (context v{ctx['version']})\n{result.log.strip()}"
            self.remember_compile(key, agent, fn, text, None)
            return {"text": text, "score": None, "matched": False}

        cand, verdict, problems = self._evaluate(fn, proj, result.obj)
        if cand is None:
            self._record(fn, agent, None, source, ctx["version"], effective_settings=getattr(result,"effective_settings",None))
            text = (f"❌ Compiled, but the object has no function named `{fn['name']}`. "
                    f"Define it with exactly that name (non-static).")
            self.remember_compile(key, agent, fn, text, None)
            return {"text": text, "score": None, "matched": False}
        d = scoring.diff(self._target_obj(fn, proj), cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        checks = self._candidate_checks(fn, proj, result.obj)
        matched_now = d.bytes_match and not scratch
        last = store.one("SELECT score FROM attempts WHERE function_id=? AND agent_id=? AND score IS NOT NULL "
                              "ORDER BY id DESC LIMIT 1", fn["id"], agent.id)
        self._record(fn, agent, d.score, source, ctx["version"], matched=matched_now, effective_settings=getattr(result,"effective_settings",None))
        self.keep_diff(agent.id, fn["id"], self._search_rows(fn, proj, result.obj, d.rows))
        compiled_output = {'fingerprint': cand.fingerprint(), 'granularity': 'function'}

        if scratch:
            head = f"(scratch — {fn['name']} was already matched) score {d.score}"
            if d.bytes_match:head += '; EXACT recheck; no new credit or source replacement'
        elif d.bytes_match:
            return {"text": f"✅ MATCH! {fn['name']} is byte-identical to the target ({fn['n_insns']} instructions). "
                            f"Credited to {agent.name}; your claim is released. Call claim_function for the next one."
                            f"{self.dependents_note(fn['id'], project_id=fn['project_id'])}"
                            + ("\n" + checks["message"] if checks.get("message") else ""),
                    "score": 0, "matched": True, "exact": True, "new_match": True, "adopted": True,
                    "checks": checks, "compiled_output": compiled_output}
        else:
            best = store.one("SELECT best_score FROM functions WHERE id=?", fn["id"])["best_score"]
            head = (f"score {d.score} ({scoring.percent_text(d.percent)} match) — best so far {best}. "
                    f"Target {len([r for r in d.rows if r[1]])} insns, yours {len([r for r in d.rows if r[2]])}.")
            head += self._what_changed(d, last["score"] if last else None, best)
            if d.rows and all(m == " " for m, _, _ in d.rows):
                head += ("\nNOT A MATCH YET: every instruction reads the same, but the bytes differ"
                         + (" because of the linking notes at the end: fix those." if problems else
                            ": an encoding the listing doesn't show (branch form, an immediate, a relocation)."))
        if checks.get("link"):
            head += "\n" + checks["link"]["message"]
        if checks.get("address_mismatches"):
            head += "\n" + checks["message"]
        warn = f"\ncompiler output:\n{result.log.strip()}" if result.log.strip() else ""
        if problems:
            warn += "\nwhen linking your code at the function's address:\n" + "\n".join(f"- {p}" for p in problems)
        from .source_lines import mismatches as source_mismatches
        from .source_lines import render as render_source_lines
        mapped = source_mismatches(result.obj, fn["name"], d.rows, source,
                                   filename="func.cpp" if getattr(result,"language","c") == "c++" else "func.c",arch=proj['arch'])
        text = f"{head}\n{d.render()}{render_source_lines(mapped, source)}{warn}"
        if not scratch:
            self.remember_compile(key, agent, fn, text, d.score)
            text += self.same_score_note(fn["id"], agent.id, d.score, source, project_id=fn["project_id"])
        selected = store.one('SELECT best_source FROM functions WHERE id=?',fn['id'])
        return {"text": text + self.stall_note(fn["id"], agent.id, project_id=fn["project_id"]), "score": d.score,
                "matched": False, "exact": d.bytes_match, "new_match": False,
                "adopted": bool(not scratch and selected and selected['best_source']==source),
                "checks": checks, "compiled_output": compiled_output}

    @staticmethod
    def _what_changed(d: scoring.DiffResult, last: int | None, best: int | None = None) -> str:
        """What the agent's last change did, and where the first difference is now: one small change per compile
        works best, and an agent that sees which way the score moved knows whether to keep or undo it."""
        said = ""
        if last is not None:
            said = ("\nYour last change: no effect on the score." if d.score == last else
                    f"\nYour last change made it better ({last} → {d.score}): keep it." if d.score < last else
                    f"\nYour last change made it worse ({last} → {d.score}): undo it, or it fixed one thing and broke another.")
        if best is not None and d.score > best:
            said = said.replace(': keep it.', '; this recovers from your previous attempt.')
            said += (f"\nSaved best is {best}; this candidate is still {d.score - best} points worse. "
                     'Use get_candidate(base="saved_best") to restore that starting point, or continue a bounded exploration. '
                     'Recovery above the inherited best does not reset the move-on budget.')
        same, total, row = scoring.first_mismatch(d.rows)
        if row is not None:
            mark, target, yours = row
            said += (f"\nThe first {same} of {total} target instructions match; the first difference "
                     f"({scoring.MARKERS.get(mark, mark)}): target `{target or '-'}`, yours `{yours or '-'}`.")
        return said

    def _search_rows(self, fn, proj, obj, fallback):
        """Advisory rows; ROM adapters can provide physical linked operands without changing verdicts."""
        return fallback

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

    def stall_status(self, function_id: int, agent_id: int, *, project_id: int | None = None) -> dict:
        """Where an agent stands against a function's move-on number: one count for compile_and_diff's results,
        the brief and release_function. The compiles of its current claim (with no claim, its last
        STALL_LOOKBACK). Compile results once counted the agent's last compiles across claims while release_function
        counted the claim's and said "no better in 8 compiles in a row" whatever the count was: in one project a
        release was refused with the last 4 of 7 no better, then let through after exactly one more compile, so
        agents spent a compile to get past the check (446 push-backs, 36 of them improved after it)."""
        store = self.project_store(project_id) if project_id is not None else self.store
        row = store.one("SELECT n_insns, project_id FROM functions WHERE id=?", function_id)
        custom = self.agent_stall_budget(row, agent_id=agent_id) if row else None
        after = custom if custom is not None else stall_budget((row["n_insns"] if row else 0) or 0)
        claim = store.one("SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?", function_id, agent_id)
        baseline = None
        if claim is not None:
            start = claim["claimed_at"] or 0
            rows = store.all("SELECT score,score_before FROM attempts WHERE function_id=? AND agent_id=? AND created_at >= ? "
                                  "ORDER BY id", function_id, agent_id, start)
            scores = [r["score"] for r in rows]
            baseline = rows[0]["score_before"] if rows else None
        else:
            start = None
            rows = store.all(f"SELECT score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC "
                                  f"LIMIT {max(STALL_LOOKBACK, after + 1)}", function_id, agent_id)
            scores = [r["score"] for r in reversed(rows)]
        return {"claimed_at": start, "compiles": len(scores), "no_better": _no_better(scores, baseline), "after": after, "inherited_best": baseline,
                "best": min((s for s in scores if s is not None), default=None)}

    def stall_note(self, function_id: int, agent_id: int, *, project_id: int | None = None) -> str:
        """Every compile result's last line: where the agent stands against the function's move-on number and, once
        it's reached, the nudge to release: functions that match mostly do in the first few tries, and the agent's
        time is better spent on the next one. Big functions get longer (a quarter of Mario Party 6's agent matches
        over 64 instructions took more than 4 compiles, and those functions hold most of the code left):
        STALL_BUDGET. release_function lets a release through exactly when this says to move on."""
        st = self.stall_status(function_id, agent_id, project_id=project_id)
        if not st["compiles"]:
            return ""
        since, after = st["no_better"], st["after"]
        if since < after:
            return f"\n\nMove-on count: {stall_line(since, after)}."
        from .release_receipts import instruction
        action=instruction(self,function_id,agent_id,project_id=project_id)
        return (f"\n\n⏭ Move on: {stall_line(since, after)}. {action}. "
                "Use this claim_token on the first release and on retries. Then claim the next function; "
                "your best attempt stays saved for whoever comes next.")

    def claim_progress(self, agent: Agent, fn) -> dict | None:
        """This agent's claim on `fn` as release_function judges it: stall_status, the same count compile_and_diff
        shows. None: no claim."""
        st = self.stall_status(fn["id"], agent.id, project_id=fn["project_id"])
        return None if st["claimed_at"] is None else st

    # ---------------------------------------------------------- prerequisites

    def wait_for(self, agent: Agent, fn, needs) -> None:
        """Record that `fn` needs `needs` done first (see WAITING)."""
        store = self.project_store(fn["project_id"])
        with store.tx() as db:
            db.execute("INSERT OR REPLACE INTO waits(function_id, needs_id, agent_id, created_at) VALUES(?,?,?,?)",
                       (fn["id"], needs["id"], agent.id, time.time()))
            self._event(db, fn["project_id"], agent.id, "waits", f"{fn['name']} -> {needs['name']}")

    def dependency_brief(self, fn) -> list[str]:
        """For a brief: what this function waits for, and what waits for it."""
        store = self.project_store(fn["project_id"])
        out = []
        row = store.one("SELECT n.name,n.status,w.agent_id FROM waits w JOIN functions n ON n.id=w.needs_id "
                        "WHERE w.function_id=?", fn['id'])
        if row is not None:
            from .app.project_labels import label
            row = label(self, [row], {'agent':'agent_id'})[0]
        if row is not None and fn["status"] != "matched":
            if row["status"] == "matched":
                out += ["", f"### {row['name']} matches now: {row['agent'] or 'an agent'} found this function needs it "
                            f"first (a callee or an inlined helper): read its code (get_function) and use it here."]
            elif store.one("SELECT 1 FROM waits WHERE function_id=(SELECT id FROM functions WHERE name=? AND "
                                "project_id=?) AND needs_id=?", row["name"], fn["project_id"], fn["id"]):
                out += ["", f"### Needs {row['name']}, which needs this one: they depend on each other (a caller and its "
                            "callee, each one's convention coming from the other), so work on them together: "
                            f"compile_and_diff(function=\"{fn['name']}, {row['name']}\", source=both definitions)."]
            else:
                out += ["", f"### Waits for {row['name']}: {row['agent'] or 'an agent'} found this function needs it "
                            f"first. This is a historical agent report, not proof that its body must match. "
                            f"Inspect the actual call and fix declarations here; do not repeat needs to skip work. "
                            f"New dependency pivots require original direct-call evidence and obey pause rules."]
        waiting = store.all("SELECT b.name FROM waits w JOIN functions b ON b.id=w.function_id WHERE w.needs_id=? "
                                 "AND b.status='open' ORDER BY b.name LIMIT 8", fn["id"])
        if waiting and fn["status"] != "matched":
            out += ["", f"### Waited for by {', '.join(r['name'] for r in waiting)}: they need this one first, so "
                        "matching it unblocks them."]
        return out

    def dependents_note(self, function_id: int, *, project_id=None) -> str:
        """For a match: the functions that were waiting for it."""
        store = self.project_store(project_id) if project_id is not None else self.store
        rows = store.all("SELECT b.name FROM waits w JOIN functions b ON b.id=w.function_id WHERE w.needs_id=? "
                              "AND b.status='open' ORDER BY b.name LIMIT 6", function_id)
        if not rows:
            return ""
        return (f"\nWaiting for this one: {', '.join(r['name'] for r in rows)}. They come first in the queue now; "
                f"claim_function(function=\"{rows[0]['name']}\") takes one next.")

    def _record(self, fn, agent: Agent, score: int | None, source: str, ctx_version: int, matched: bool = False,
                compiled: bool | None = None, via: str = "agent", effective_settings=None,
                prepared_header=None, prepared_inputs=None, prepared_compiler=None, before_record=None, restoring_exact=False, prepared_assembly=False, write_wait=None, exact_inputs=None):
        store = self.project_store(fn["project_id"])
        now = time.time()
        compiled = score is not None if compiled is None else compiled
        improved = False
        with store.tx(wait=write_wait) as db:
            if before_record is not None:
                before_record()  # scoped callers can reject revoked or stale work before any writes
            current = db.execute("SELECT * FROM functions WHERE id=?", (fn["id"],)).fetchone()
            if exact_inputs is not None:
                if not matched or score != 0:
                    raise NetworkError('Only a freshly verified exact match can preserve its compile context.')
                if (source != exact_inputs['source'] or
                        (effective_settings or {}).get('compiler', exact_inputs['compiler']) != exact_inputs['compiler']):
                    raise NetworkError('The compiled source or profile differs from the captured exact-match inputs. Recheck the match.')
                from .app.exact_match_inputs import publish
                ctx_version = publish(self, db, current, exact_inputs, ctx_version, agent)
                current = db.execute('SELECT * FROM functions WHERE id=?', (fn['id'],)).fetchone()
            if prepared_assembly:
                if not (matched and score==0 and prepared_header is not None and prepared_inputs is not None and current['status']=='asm'):
                    raise NetworkError('Assembly reconsideration requires an explicitly approved exact prepared bundle.')
                from .app.source_preparation import inputs
                proj=db.execute('SELECT * FROM projects WHERE id=?',(current['project_id'],)).fetchone()
                if inputs(self,proj,current)!=prepared_inputs:
                    raise NetworkError('Assembly candidate inputs changed. Run its source check again.')
                meta={k:v for k,v in json.loads(current['meta'] or '{}').items() if k not in ('not_c','asm_code','asm_twin')}
                db.execute("UPDATE functions SET status='open',meta=? WHERE id=?",(json.dumps({**meta,'reopened':True}),current['id']))
                self._event(db,current['project_id'],agent.id,'reopen',current['name']+': exact local source bundle verified')
                current=db.execute('SELECT * FROM functions WHERE id=?',(fn['id'],)).fetchone()
            if prepared_compiler is not None:
                if not matched or prepared_header is None or score != 0:
                    raise NetworkError("Only a reverified exact prepared bundle can change its compiler profile.")
                from .app.prepared_evidence import publish_profile
                prepared_inputs = publish_profile(self, db, current, prepared_inputs, prepared_compiler, agent)
            # A compiler check can finish while this candidate is still compiling with the old flags.
            # Keep that attempt as evidence, but never turn its old-profile verdict into a new green status.
            config = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (fn["project_id"],)).fetchone()["config"])
            used = (effective_settings or {}).get("compiler")
            own = compiler_for(config, current["vram"], current) if used else None
            stale = bool(score is not None and used and any(used.get(k) != own.get(k) for k in ("toolchain", "flags")))
            if restoring_exact and not (matched and score==0 and prepared_header is not None and prepared_inputs is not None):
                raise NetworkError('Saved-match restoration requires a fresh exact source/header verdict.')
            if prepared_header is not None:
                from .app.source_preparation import promote_context
                if stale or score is None or (current['best_score'] is not None and score >= current['best_score']
                        and not (restoring_exact and current['status']=='open' and matched and score==0)):
                    raise NetworkError("The prepared candidate no longer improves this function. Run the pass again.")
                ctx_version = promote_context(self, db, current, prepared_inputs, prepared_header, agent)
                current = db.execute("SELECT * FROM functions WHERE id=?", (fn['id'],)).fetchone()
            attempt = db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at,via) "
                       "VALUES(?,?,?,?,?,?,?,?)", (fn["id"], agent.id, score, compiled, ctx_version, source, now, via)).lastrowid
            self.repair_feedback.attempt(db, current, attempt, ctx_version, effective_settings)
            if matched and not stale:
                from .match_bundle import capture as capture_bundle
                capture_bundle(db,current,attempt,source,ctx_version,effective_settings)
                self.repair_feedback.capture(db, current, source, agent.id, via, ctx_version, attempt)
            if not matched and not stale and (not used or all(used.get(k)==own.get(k) for k in ("toolchain","flags"))):
                from .match_bundle import failed_recheck
                if failed_recheck(db,fn,attempt,source,score):
                    self._event(db,fn['project_id'],agent.id,'regression',fn['name'])
            db.execute("UPDATE functions SET attempts=attempts+1,last_checked=? WHERE id=?", (now, fn["id"]))
            from . import candidate_quality
            reported = not matched and candidate_quality.reported_source(self, current, source)
            if score is not None and not stale and not reported:
                improved = db.execute("UPDATE functions SET best_score=?, best_source=?, best_agent_id=?, last_improved=CASE WHEN ? THEN ? ELSE last_improved END "
                                      "WHERE id=? AND status='open' AND (best_score IS NULL OR best_score > ?)",
                                      (score, source, agent.id, score < fn["n_insns"] * 100, now, fn["id"], score)).rowcount
                if improved:
                    from .app.permuter_inputs import external_seed
                    external_seed(db, current, source, agent.name)
            if matched and not stale:
                db.execute("UPDATE functions SET status='matched', matched_source=?, matched_agent_id=?, "
                           "matched_at=?, best_score=0, best_source=? WHERE id=?",
                           (source, agent.id, now, source, fn["id"]))
                self._release_matched_claim(db,fn['id'],agent.id)
                self._event(db, fn["project_id"], agent.id, "match", fn["name"])
            db.execute('UPDATE attempts SET score_before=?,score_improved=? WHERE id=?',
                       (current['best_score'], int(bool(improved or (matched and not stale and current['status'] != 'matched'))), attempt))

        if stale:
            raise NetworkError("Compiler settings changed while this candidate was compiling. Your attempt is saved; compile it again with today's settings.")
        if not matched and fn["status"] == "open":
            try:
                self.check_attempt_limit(agent, fn)
            except NetworkError:
                pass  # the attempt itself still returns its diff; the next claim moves on
        if matched:
            store.after_commit(lambda: self.on_match(store.one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn["id"], agent))
        elif improved:
            self.on_improvement(store.one("SELECT * FROM projects WHERE id=?", fn["project_id"]), fn["id"])

    def _release_matched_claim(self, db, function_id, matching_agent_id=None):
        """The saved match owns the function; notify its displaced worker only after commit."""
        owner=db.execute('SELECT c.agent_id,f.name,f.project_id FROM claims c JOIN functions f ON f.id=c.function_id '
                         'WHERE c.function_id=?',(function_id,)).fetchone()
        if owner and owner['agent_id']!=matching_agent_id and (notify:=getattr(self,'on_external_match',None)):
            def handoff():
                try:notify(owner['agent_id'],owner['name'],owner['project_id'])
                except Exception:
                    # Worker notification must never turn a committed match into a failed save.
                    logging.getLogger('decompdecomp').exception('Could not redirect worker after an external match')
            self.project_store(owner['project_id']).after_commit(handoff)
        db.execute('DELETE FROM claims WHERE function_id=?',(function_id,))

    def on_improvement(self, proj, function_id: int) -> None:
        """A better unfinished candidate was saved; subclasses may schedule bounded reuse."""

    def on_match(self, proj, function_id: int, agent: Agent | None = None) -> None:
        """A function just matched (subclasses try its code on its twins)."""

    # -------------------------------------------------------- shared context

    def edit_context(self, agent: Agent, project: str | None, old: str, new: str, message: str,
                     function: str | None = None, *, progress=None) -> str:
        """Exact-string edit of a header. Accepted only if every already-matched function compiled against
        that header still matches with the new one — that's the project's test suite. `progress`: told how far
        that check has got."""
        if function:
            fn = self._function(function, project)
            proj = self.project_store(fn["project_id"]).one("SELECT * FROM projects WHERE id=?", fn["project_id"])
            unit = self._effective_unit(fn, self._unit_contexts(proj["id"]))
        else:
            proj = self._project(project)
            if self._unit_contexts(proj["id"]):
                raise NetworkError(f"{proj['slug']} has one header per source file: pass `function` to edit the "
                                   f"header that function compiles against")
            unit = ""
        store = self.project_store(proj["id"])
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
            from .context_preflight import conflicts
            incompatible=conflicts(new_header,members)
            if incompatible:
                listing='\n'.join(f'- {fn}: conflicting extern {name}' for fn,name in incompatible[:8])
                return ('❌ REJECTED by declaration preflight; no regression compiles run. Keep declarations candidate-local.\n'+listing)
            # Rejections only: exact header bytes and all judge inputs distinguish trials. A short
            # process-local lifetime also limits effects of external tool/environment changes.
            symbols = store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',proj['id'])
            state = repr((dict(proj),unit,ctx['version'],header,new_header,
                          [dict(m) for m in members],[dict(row) for row in symbols],
                          self._generation.get(proj['id'],0)))
            key=hashlib.sha256(state.encode()).hexdigest()
            with store.lock:
                gate=self._context_trial_locks.setdefault((proj['id'],unit),threading.RLock())
            with gate:
                cached=self._context_rejections.get(key)
                if cached and time.monotonic()-cached[0]<120:
                    if self._context(proj['id'],unit)['version']!=ctx['version']:
                        continue
                    return 'Cached rejected header edit (no regression compiles run).\n'+cached[1]
                broken = self.regression_test(proj, new_header, only=members, progress=progress)
                if broken:
                    listing = "\n".join(f"- {name}: {why}" for name, why in broken[:10])
                    result=(f"❌ REJECTED: the edit breaks {len(broken)} already-matched function(s), so the header "
                            f"stays at v{ctx['version']}.\n{listing}\nKeep the declaration candidate-local unless a safe shared change is needed.")
                    self._context_rejections[key]=(time.monotonic(),result)
                    while len(self._context_rejections)>32:
                        self._context_rejections.popitem(last=False)
                    return result
            with store.tx() as db:
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
        store = self.project_store(proj["id"])
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
            matched = store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
            rewritten = {r["id"]: word.sub(new, r["matched_source"]) for r in matched}
            header_for = lambda fn: changed.get(self._effective_unit(fn, units), current[self._effective_unit(fn, units)]["header"])  # noqa: B023  called within this pass of the loop
            broken = self.regression_test(proj, None, sources=rewritten, only=matched, header_for=header_for)
            if broken:
                listing = "\n".join(f"- {name}: {why}" for name, why in broken[:10])
                return f"❌ REJECTED: renaming breaks {len(broken)} matched function(s).\n{listing}"
            msg = message.strip() or f"rename {old} -> {new}"
            with store.tx() as db:
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
        store = self.project_store(proj["id"])
        units = self._unit_contexts(proj["id"])
        # Filter effective headers before the packed-text view decodes bodies.
        # Units without their own header use the shared header.
        return store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND "
                         "CASE WHEN unit IN (SELECT value FROM json_each(?)) THEN unit ELSE '' END=?",
                         proj["id"], json.dumps(sorted(units)), unit)

    def regression_test(self, proj, header: str | None, sources: dict[int, str] | None = None,
                        only: list | None = None, header_for=None, progress=None) -> list[tuple[str, str]]:
        """Every matched function (or `only`) built again with a changed header or sources: (name, why) of each
        that no longer matches, so a header edit that breaks matches is refused."""
        store = self.project_store(proj["id"])
        matched = only if only is not None else store.all(
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
        broken = []
        with self._pool.group('Matched function verification', proj['slug']) as pool:
            for done, result in enumerate(pool.map(check, matched), 1):
                if result:
                    broken.append(result)
                if progress:
                    progress({"phase": "Checking affected matches", "done": done, "total": len(matched)})
        return broken

    # ------------------------------------------------------------------ notes

    def add_note(self, agent: Agent, project: str | None, text: str, function: str | None = None, *, score_before=None, score_after=None) -> str:
        """A note for the agents after this one, on a function or on the whole project."""
        proj = self._project(project)
        fn = self._function(function, proj["slug"]) if function else None
        if not text.strip():
            raise NetworkError("empty note")
        with self.project_store(proj["id"]).tx() as db:
            measured = (score_before, score_after) if all(type(v) is int and v >= 0 for v in (score_before, score_after)) else None
            # Keep one current deep-search summary per runner/function. Other function
            # notes append to their managed session handoff when session evidence exists.
            if fn and text.startswith('Deep permuter:'):
                row = db.execute("SELECT id FROM notes WHERE function_id=? AND agent_id=? "
                                 "AND text LIKE 'Deep permuter:%' ORDER BY id DESC LIMIT 1",
                                 (fn['id'], agent.id)).fetchone()
                if row:
                    db.execute('UPDATE notes SET text=?,created_at=?,score_before=?,score_after=? WHERE id=?',
                               (text.strip()[:NOTE_CHARS], time.time(), *(measured or agent_notes.current_scores(db, fn['id'], time.time())), row['id']))
                    return 'Updated the deep permuter summary.'
            agent_notes.save(db, proj["id"], fn["id"] if fn else None, agent.id, text.strip()[:NOTE_CHARS], measured=measured, agent_name=agent.name)
        return "Noted. Other agents will see it" + (f" when they open {fn['name']}." if fn else " in their briefings.")

    def search_notes(self, project: str | None, query: str) -> str:
        """Notes on the project that contain `query`, newest first."""
        proj = self._project(project)
        rows = self.project_store(proj["id"]).all(
            "SELECT n.text,n.created_at,n.agent_id,f.name AS fn FROM notes n "
            "LEFT JOIN functions f ON f.id=n.function_id "
            f"WHERE n.project_id=? AND n.text LIKE ? ORDER BY n.id DESC LIMIT {NOTES_FOUND}", proj["id"], f"%{query}%")
        from .app.project_labels import label
        rows = label(self, rows, {'agent':'agent_id'})
        if not rows:
            return f"No notes matching {query!r}."
        return "\n".join(f"- [{r['fn'] or 'project'}] ({r['agent']}, {_ago(r['created_at'])}) {r['text']}" for r in rows)

    # ------------------------------------------------------------- the project as a whole

    def remove_project(self, slug: str) -> int:
        """Remove a project from the catalog, preserving fenced recovery files after upgrade."""
        if self.store.one('SELECT 1 FROM game_variants WHERE project=?', slug):
            raise NetworkError('Detach this variant from its game before removing the project.')
        proj = self._project(slug)
        if self._project_files is not None:
            count = self._project_files.remove(proj)
            if progress := getattr(self,"progress",None):progress.forget(slug,proj['id'])
            return count
        with self.store.tx() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM functions WHERE project_id=?", (proj["id"],))]
            for table in ("claims", "attempts"):
                db.executemany(f"DELETE FROM {table} WHERE function_id=?", [(i,) for i in ids])
            for table in ("notes", "events", "context_versions", "functions", "symbols", "twin_misses",
                          "cross_game_index", "library_transfers"):
                db.execute(f"DELETE FROM {table} WHERE project_id=?", (proj["id"],))
            db.execute("DELETE FROM cross_game_attempts WHERE project_id=? OR source_project_id=?",
                       (proj["id"], proj["id"]))
            db.execute("DELETE FROM projects WHERE id=?", (proj["id"],))
        if progress := getattr(self, "progress", None):
            progress.forget(slug, proj["id"])
        return len(ids)
