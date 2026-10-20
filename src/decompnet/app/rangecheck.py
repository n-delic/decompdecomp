"""Range checks: an agent's "this address range was built by another compiler" is a hypothesis, and decompdecomp tests
it on the functions next to it before the range applies.

GTA San Andreas gathered 72 compiler ranges in a day, many applied as soon as one function's C matched with another
compiler: CodeWarrior and GCC 2.9 ranges alternated within a few hundred bytes. One function proves little: a few
instructions often come out the same from two compilers, and C that isn't the original's can happen to match with
the wrong one (a quirk). The linker lays out one object file after another, and an object file is one compiler, so a
real range shows on its neighbours too.

A check walks out from the function that started it, on both sides, and gives each neighbour a verdict:
- confirmed: its C matches the game with the range's compiler and not with today's (the judge, both compilers);
- refuted: it's matched with today's compiler and its C doesn't match with the range's, or its code shows a habit
  the range's compiler never has (compilermap: -G0 code never reads through $gp, ...), or the judge's survey
  confirmed another compiler for it;
- neutral: its C matches with both (too little code to tell), or it's too short to test;
- unknown: no C that matches yet.
Free evidence first (matches, attempts agents already made, the survey, habits); where none settles it, the nearest
unknown neighbour becomes a task: it's put first in line for the next agent, with a note saying what to test. An
agent that gives up on it says nothing about the compiler (inconclusive, not a quirk): the next neighbour is tried.

The verdict: confirmed when a second function confirms it (the range then covers the confirmed functions and what
lies between them, never past a refuted one, and applies itself if the user lets proven compiler changes apply);
a quirk when the nearest testable neighbours on both sides are refuted and nothing else confirms it (the suggestion
is dismissed and the function gets a note: find C that matches with the game's compiler); waiting when the tasks
ran out without an answer.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import logging
import threading
import time
from dataclasses import dataclass

from .. import compilermap
from ..network import NetworkError
from ..project import compiler_for

MIN_INSNS = 12  # a shorter function comes out the same from several compilers: it can't confirm or refute anything
REACH = 3  # testable neighbours looked at on each side, beyond the suggested ranges
MAX_TASKS = 4  # neighbours given to agents before a check waits for the user
OPEN = ("checking", "waiting")  # a check's states while it can still change
# GTA San Andreas: every match started a recheck of its 62 open checks, 17 s each, and matches came faster than that
# (92 rechecks queued at once). Now a project's rechecks wait this long after the last request and run as one.
RECHECK_DEBOUNCE_SECONDS = 2.0
VERDICTS_KEPT = 4096  # judge verdicts remembered: the same C with the same compiler gives the same answer
# What a check reads of every function, once per run. Never the assembly: GTA San Andreas's is 73 MB, and reading
# every column (`SELECT *`) for each of the 62 checks was 14 of the 17 seconds.
ROW_COLUMNS = "id, name, vram, size, n_insns, status"
# what the judge needs, read only for the few neighbours a check gives a verdict
JUDGE_COLUMNS = "id, project_id, name, unit, vram, size, n_insns, status, target, relocs, matched_source, best_source"
# why checks were dismissed when the suggested compiler's flags didn't count (RangeChecks.reopen)
WRONG_QUIRK = "matches the game with today's compiler too"
log = logging.getLogger("decompdecomp")


@dataclass
class Step:
    vram: int
    end: int
    verdict: str  # confirmed | refuted | neutral | unknown | origin


def evaluate(left: list[Step], origin: Step, right: list[Step]) -> tuple[str, int, int, Step | None]:
    """(status, start, end, next to test) from the verdicts walking out from the origin (left: nearest first).
    status: confirmed | quirk | checking. start-end: the range the evidence supports (confirmed functions and what
    lies between them, up to the first refuted one on each side)."""
    start, end, confirmed, walls = origin.vram, origin.end, 1 if origin.verdict in ("origin", "confirmed") else 0, 0
    unknown: list[tuple[int, int, Step]] = []  # (distance, side, step): the nearest one is tested next
    for n, side in enumerate((left, right)):
        reach = None
        for i, step in enumerate(side):
            if step.verdict == "refuted":
                walls += step is next((s for s in side if s.verdict != "neutral"), None)
                break
            if step.verdict == "unknown":
                unknown.append((i, n, step))
                break
            if step.verdict == "confirmed":
                confirmed += 1
                reach = step
        if reach is not None:
            start, end = min(start, reach.vram), max(end, reach.end)
    if confirmed >= 2:
        return "confirmed", start, end, None
    if walls == 2 and confirmed <= 1:
        return "quirk", origin.vram, origin.end, None
    return "checking", origin.vram, origin.end, min(unknown, key=lambda u: u[:2])[2] if unknown else None


class RangeChecks:
    """The checks of every project, kept in its settings (cfg["range_checks"]), worked on in the background."""

    background = True  # checks compile: in a thread of their own (tests run them in place)

    def __init__(self, net):
        self.net = net
        self._lock = threading.Lock()  # one run at a time
        self._queue_lock = threading.Lock()
        self._pending: dict[str, set] = {}  # slug -> ids of the checks to weigh again (None: every open one)
        self._draining: set[str] = set()  # projects with a run waiting or running
        self._verdicts: dict[tuple, bool] = {}  # see _judged

    # ------------------------------------------------------------------ starting one

    def propose(self, proj, suggestion: dict, function: str | None, toolchain: str, flags: list[str],
                spans: list[list[int]], source: str = "") -> str:
        """An agent suggested ranges for another compiler: test them before they apply. Returns what the agent is told."""
        fn = self.net._function(function, proj["slug"]) if function else None
        check = {"id": 0, "suggestion": suggestion["id"], "toolchain": toolchain, "flags": list(flags),
                 "origin": fn["vram"] if fn else None, "origin_source": source or None, "spans": spans,
                 "status": "checking", "evidence": {}, "tasks": [], "given_up": [], "range": None, "t": time.time()}

        def add(cfg):
            items = cfg.setdefault("range_checks", [])
            same = next((c for c in items if c["status"] in ("checking", "waiting") and c["toolchain"] == toolchain
                         and c["flags"] == check["flags"] and c["origin"] == check["origin"]), None)
            if same is None:
                check["id"] = max((c["id"] for c in items), default=0) + 1
                items.append(check)
            found.append(same or check)
        found: list[dict] = []
        self.net._update_cfg(proj["slug"], add)
        c = found[0]
        self.recheck(proj["slug"], c["id"])
        return (f"Range check #{c['id']} started: decompdecomp tests the functions next to it with the judge before the "
                f"range applies (one function proves little: C can match with the wrong compiler by chance). "
                f"compiler_map() shows how it goes; a neighbour may come to an agent as a task.")

    # ------------------------------------------------------------------ evidence

    def _rows(self, proj) -> list:
        return self.net.store.all(f"SELECT {ROW_COLUMNS} FROM functions WHERE project_id=? AND vram IS NOT NULL "
                                  "ORDER BY vram", proj["id"])

    @staticmethod
    def _at(rows, vram: int):
        return next((r for r in rows if r["vram"] == vram), None)

    def _open(self, proj) -> list[dict]:
        """The open checks, each a copy of its own to change (the settings they're read from are shared)."""
        return [copy.deepcopy(c) for c in self.net.cfg(proj).get("range_checks", []) if c["status"] in OPEN]

    def _judged(self, proj, fn, label, source: str) -> bool:
        """The judge's verdict on C built with a compiler, remembered by the C's hash: a recheck after every match
        asked the same questions again, two compiles for each neighbour. A new header version asks again."""
        ctx = self.net._context(proj["id"], fn["unit"])
        # shortcut: a renamed symbol can change what C links to, and a remembered verdict doesn't see it until the
        # app restarts; key verdicts by the symbols' version too if a check ever goes wrong that way
        key = (fn["id"], fn["size"], label, hashlib.sha1(source.encode("utf-8", "replace")).hexdigest(),
               (ctx["unit"], ctx["version"]) if ctx else None)
        if key not in self._verdicts:
            if len(self._verdicts) >= VERDICTS_KEPT:
                self._verdicts.pop(next(iter(self._verdicts)))  # the oldest
            self._verdicts[key] = self.net._judge_with(proj, fn, label[0], list(label[1]), source)[0]
        return self._verdicts[key]

    def _classify(self, proj, cfg, fn, label, cmap, survey: dict, source: str | None = None) -> str:
        """One function's verdict on "built by `label`" (see the module's docstring)."""
        if (fn["n_insns"] or 0) < MIN_INSNS:
            return "neutral"
        own = compiler_for(cfg, fn["vram"])
        built = (own["toolchain"], tuple(own["flags"]))  # what it's built with now (and matched with, if it is)
        kind = compilermap.kind(label)
        # the other side of the question: today's compiler, or the game's own where the range already applies. Whole
        # labels, flags and all: by kind, IDO -O1 or -framepointer was the same as an IDO -O2 game's compiler, and 85
        # suggestions on an N64 game were dismissed without a compile, functions that match only with them included
        other = built if built != label else (cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))
        if other == label:
            return "neutral"  # the range's compiler is the game's: nothing to tell apart
        fn = self.net.store.one(f"SELECT {JUDGE_COLUMNS} FROM functions WHERE id=?", fn["id"]) or fn
        if cmap is not None and fn["target"] and cmap.never.get(kind) and (
                set(cmap.never[kind]) & set(compilermap.quirks(fn["target"], bool(cfg.get("big_endian", True))))):
            return "refuted"
        said = survey.get(str(fn["vram"]))
        if said:
            return "confirmed" if (said[0], tuple(said[1])) == label else "refuted"
        judge = lambda lab, src: self._judged(proj, fn, lab, src)  # noqa: E731
        if fn["status"] == "matched" and fn["matched_source"]:  # it matches with what it's built with: try the other
            src = fn["matched_source"]
            if built == label:
                return "neutral" if judge(other, src) else "confirmed"
            return "neutral" if judge(label, src) else "refuted"
        src = source or self.net._source_of(fn)
        if not src or not judge(label, src):
            return "unknown"
        return "neutral" if judge(other, src) else "confirmed"

    def recheck(self, slug: str, cid: int | None = None, background: bool = True) -> None:
        """Weigh the evidence of a check (or all open ones) again: after it starts, a match, a try_compiler, a give-up.
        In the background, one run per project: it starts a moment after the last request, so a burst of matches is
        one run, and requests made while it runs make one more run after it."""
        if not (background and self.background):
            self._run(slug, {cid})
            return
        with self._queue_lock:
            self._pending.setdefault(slug, set()).add(cid)
            if slug in self._draining:
                return
            self._draining.add(slug)
        threading.Thread(target=self._drain, args=(slug,), daemon=True, name=f"range-check-{slug}").start()

    def _drain(self, slug: str) -> None:
        try:
            while True:
                time.sleep(RECHECK_DEBOUNCE_SECONDS)
                with self._queue_lock:
                    wanted = self._pending.pop(slug, set())
                    if not wanted:
                        self._draining.discard(slug)
                        return
                self._run(slug, wanted)
        finally:
            with self._queue_lock:
                self._draining.discard(slug)

    def _run(self, slug: str, wanted: set) -> None:
        """Weigh the wanted checks (None: every open one), reading every function's address and size, and the
        compiler map, once for all of them."""
        with self._lock:
            try:
                proj = self.net._project(slug)
                checks = [c for c in self._open(proj) if None in wanted or c["id"] in wanted]
                if not checks:
                    return
                rows = self._rows(proj)
                at = {r["vram"]: i for i, r in enumerate(rows)}
                cmap = self.net.compiler_map(proj)
                for c in checks:
                    self._weigh(proj, c, rows, at, cmap)
            except Exception:  # noqa: BLE001  a check never stands between an agent and its work: it waits
                log.exception("range check failed")

    def _weigh(self, proj, c: dict, rows: list, at: dict[int, int], cmap) -> None:
        """One check, from its functions in address order (`at`: address -> index) and the compiler map."""
        cfg = self.net.cfg(proj)
        label = (c["toolchain"], tuple(c["flags"]))
        survey = (cfg.get("compiler_survey") or {}).get("confirmed", {})
        if not rows:
            return
        inside = lambda r: any(a <= r["vram"] < b for a, b in c["spans"])  # noqa: E731
        if c["origin"] in at:
            o = at[c["origin"]]
        else:  # no function of its own (ranges alone, or an audit): the first function in the ranges stands for them
            firsts = [i for a, b in c["spans"] if (i := bisect.bisect_left(rows, a, key=lambda r: r["vram"])) < len(rows)
                      and rows[i]["vram"] < b]
            if not firsts:
                return
            o = min(firsts)
        evidence = dict(c["evidence"])

        def verdict(r) -> str:
            key = str(r["vram"])
            if evidence.get(key) in ("confirmed", "refuted"):
                return evidence[key]
            evidence[key] = self._classify(proj, cfg, r, label, cmap, survey)
            if evidence[key] == "unknown" and key in c["given_up"]:
                return "neutral"  # an agent gave up on it: no evidence either way, the walk goes past it
            return evidence[key]
        origin = rows[o]
        if c["origin"] in at:
            v = self._classify(proj, cfg, origin, label, cmap, survey, c.get("origin_source"))
            short = (origin["n_insns"] or 0) < MIN_INSNS  # too short to tell anything: its neighbours decide
            if v == "neutral" and not short:  # its own C matches with today's compiler too: no evidence at all
                return self._finish(proj, c, rows, "quirk", origin["vram"], origin["vram"] + origin["size"], evidence,
                                    f"{origin['name']}'s C matches the game with today's compiler too: it says nothing "
                                    "about the compiler")
            first = Step(origin["vram"], origin["vram"] + origin["size"],
                         "neutral" if v == "neutral" else "origin" if v != "refuted" else "refuted")
        else:
            first = Step(origin["vram"], origin["vram"] + origin["size"], verdict(origin))

        def walk(rng) -> list[Step]:
            out, outside = [], 0
            for i in rng:
                r = rows[i]
                if not inside(r):
                    outside += (r["n_insns"] or 0) >= MIN_INSNS
                    if outside > REACH:
                        break
                out.append(Step(r["vram"], r["vram"] + r["size"], verdict(r)))
                if out[-1].verdict in ("refuted", "unknown"):
                    break
            return out
        left, right = walk(range(o - 1, -1, -1)), walk(range(o + 1, len(rows)))
        status, start, end, nxt = evaluate(left, first, right)
        c["evidence"] = evidence
        if status == "confirmed":  # replicated: the suggested ranges stand, cut back only where a neighbour refutes them
            start, end = min([start] + [a for a, _ in c["spans"]]), max([end] + [b for _, b in c["spans"]])
            start = max([start] + [s.end for s in left if s.verdict == "refuted"])
            end = min([end] + [s.vram for s in right if s.verdict == "refuted"])
        if status != "checking":
            return self._finish(proj, c, rows, status, start, end, evidence)
        self._task(proj, c, nxt, rows, at)

    # ------------------------------------------------------------------ verdicts and tasks

    def _task(self, proj, c: dict, nxt: Step | None, rows, at) -> None:
        """Give the nearest unknown neighbour to the next agent (first in line, with a note), or wait."""
        if c.get("audit"):  # an applied range, looked at again: free evidence only, agents keep their work
            c["status"] = "waiting"
            self._save(proj, c, "unsettled: no neighbour confirms or refutes it yet")
            return
        given = set(c["tasks"])
        if nxt is not None and nxt.vram in given:
            return  # an agent has it, or will: its answer moves the check on
        if nxt is not None and len(given) < MAX_TASKS:
            fn = rows[at[nxt.vram]]
            c["tasks"] = c["tasks"] + [nxt.vram]
            self.net.set_priority(proj["slug"], fn["name"], True)
            agent, _ = self.net.local_agent("autopilot")
            self.net.add_note(agent, proj["slug"], self.task_text(c, fn["name"]), fn["name"])
            self._save(proj, c, f"checking: {fn['name']} is next (first in line for an agent)")
            return
        c["status"] = "waiting"
        self._save(proj, c, "waiting: no neighbour settled it yet; the user decides, or agents' next matches do")

    def task_text(self, c: dict, name: str) -> str:
        short = self.net._short((c["toolchain"], tuple(c["flags"])))
        return (f"Range check #{c['id']}: is this part of the game built by {short}? An agent's C for a function "
                f"next to {name} matches with it. Match {name} as usual; then try_compiler(\"{name}\", "
                f"\"{c['toolchain']}\", \"{' '.join(c['flags'])}\"). Matching with {short} and not with the project's "
                f"compiler confirms the range; matching with the project's compiler only shows it was a quirk. "
                f"Either answer helps.")

    def _finish(self, proj, c: dict, rows, status: str, start: int, end: int, evidence: dict, why: str = "") -> None:
        c["status"], c["range"], c["evidence"] = status, [start, end], evidence
        short = self.net._short((c["toolchain"], tuple(c["flags"])))
        agent, _ = self.net.local_agent("autopilot")
        confirmed = sum(v == "confirmed" for v in evidence.values()) + (c["origin"] is not None)
        if c.get("audit"):
            text = (f"confirmed by {confirmed} functions" if status == "confirmed" else
                    f"looks like a quirk ({why or 'the functions on both sides are built by the game’s compiler'}): "
                    f"the user can remove the range")
            self._save(proj, c, text)
        elif status == "confirmed":
            text = f"confirmed by {confirmed} functions: 0x{start:X}-0x{end:X} is built by {short}"
            applied = ""
            if self.net.auto_adjusts(proj):
                try:
                    self.net.set_compiler_ranges(proj["slug"], [[start, end]], c["toolchain"], c["flags"])
                    applied = " (applied)"
                except NetworkError as e:
                    applied = f" (not applied: {e})"
            self._save(proj, c, text + applied, resolve="applied" if applied == " (applied)" else None)
        else:
            text = f"a quirk: {why or 'the functions on both sides are built by the project’s compiler'}"
            if c["origin"] is not None:
                fn = self._at(rows, c["origin"])
                if fn:
                    self.net.add_note(agent, proj["slug"],
                                      f"Range check #{c['id']}: this function's C matches with {short}, but its "
                                      f"neighbours are built by the project's compiler ({why or 'they say so'}). "
                                      f"Look for C that matches with the project's compiler.", fn["name"])
            self._save(proj, c, text, resolve="dismissed")
        for vram in c["tasks"]:  # its tasks are done: out of the line
            fn = self._at(rows, vram)
            if fn:
                self.net.set_priority(proj["slug"], fn["name"], False)
        with self.net.store.tx() as db:
            self.net._event(db, proj["id"], None, "compiler", f"range check #{c['id']} ({short}): {text}")

    def _save(self, proj, c: dict, said: str, resolve: str | None = None) -> None:
        c["said"] = said
        stored = next((old for old in self.net.cfg(self.net._project(proj["slug"])).get("range_checks", [])
                       if old["id"] == c["id"]), None)
        if c == stored and resolve is None:
            return  # nothing new (GTA San Andreas's 60 unsettled audits were written again on every run)

        def change(cfg):
            for i, old in enumerate(cfg.get("range_checks", [])):
                if old["id"] == c["id"]:
                    cfg["range_checks"][i] = c
            for s in cfg.get("suggestions", []):
                if s["id"] == c["suggestion"]:
                    s["check"] = f"range check #{c['id']}: {said}"
                    if c["status"] == "confirmed":  # applying it applies what the evidence supports
                        s["ranges"] = [c["range"]]
                    if resolve and s["status"] == "open":
                        s["status"] = resolve
        self.net._update_cfg(proj["slug"], change)

    # ------------------------------------------------------------------ what agents do

    def checks_on(self, proj, vram: int) -> list[dict]:
        """Open checks with this function among their tasks."""
        return [c for c in self._open(proj) if vram in c["tasks"]]

    def tried(self, proj, fn, toolchain: str, flags: list[str], source: str, matched: bool) -> None:
        """try_compiler on a function: C that matches with an open check's compiler (and not today's) confirms it."""
        if not matched or (fn["n_insns"] or 0) < MIN_INSNS:
            return
        for c in self._open(proj):
            if c["toolchain"] == toolchain and c["flags"] == list(flags):
                cfg = self.net.cfg(proj)
                own = compiler_for(cfg, fn["vram"])
                if not self.net._judge_with(proj, fn, own["toolchain"], list(own["flags"]), source)[0]:
                    c["evidence"][str(fn["vram"])] = "confirmed"
                    c["status"] = "checking"
                    self._save(proj, c, f"{fn['name']} matches with it")
                    self.recheck(proj["slug"], c["id"])

    def gave_up(self, proj, fn) -> None:
        """An agent released a task: inconclusive (failing says nothing about the compiler); the next neighbour."""
        for c in self.checks_on(proj, fn["vram"]):
            self.net.set_priority(proj["slug"], fn["name"], False)  # not the next agent's first task again
            c["given_up"] = c["given_up"] + [str(fn["vram"])]
            c["status"] = "checking"
            self._save(proj, c, f"{fn['name']}: the agent gave up (inconclusive); the next neighbour")
            self.recheck(proj["slug"], c["id"])

    def audit(self, proj) -> int:
        """Look at every applied range again, with free evidence only (no tasks): a report, nothing changes by itself."""
        ranges = self.net.cfg(proj).get("compiler_ranges", [])

        def add(cfg):
            items = cfg.setdefault("range_checks", [])
            known = {(tuple(map(tuple, c["spans"])), c["toolchain"]) for c in items if c.get("audit")}
            for r in ranges:
                if (((r["start"], r["end"]),), r["toolchain"]) not in known:
                    items.append({"id": max((c["id"] for c in items), default=0) + 1, "suggestion": None, "audit": True,
                                  "toolchain": r["toolchain"], "flags": list(r["flags"]), "origin": None,
                                  "spans": [[r["start"], r["end"]]], "status": "checking", "evidence": {}, "tasks": [],
                                  "given_up": [], "range": None, "t": time.time()})
                    added.append(1)
        added: list[int] = []
        self.net._update_cfg(proj["slug"], add)
        self.recheck(proj["slug"])
        return len(added)

    def reopen(self, slug: str) -> int:
        """Once: weigh again the checks dismissed as quirks before checks told compilers apart by their flags. Their
        compiler was the game's toolchain with other flags (IDO -O1 or -framepointer on an IDO -O2 game), which counted
        as today's compiler: no compile was made, and the function got a note sending agents after C that matches with
        the project's compiler, which some never can. The note goes, and one check for each compiler and function is
        weighed again (the same suggestion made again stays dismissed). Returns how many."""
        proj = self.net._project(slug)
        cfg = self.net.cfg(proj)
        game = (cfg["compiler"]["toolchain"], tuple(cfg["compiler"]["flags"]))
        again: list[dict] = []

        def change(cfg):
            seen = set()
            for c in cfg.get("range_checks", []):
                label = (c["toolchain"], tuple(c["flags"]))
                if c.get("reopened") or c["status"] != "quirk" or WRONG_QUIRK not in c.get("said", "") or \
                        label == game or compilermap.kind(label) != compilermap.kind(game):
                    continue
                c["reopened"] = True
                if (label, c["origin"]) in seen:
                    continue
                seen.add((label, c["origin"]))
                c.update(status="checking", evidence={}, tasks=[], given_up=[], range=None, said="weighed again")
                again.append(c)
                for s in cfg.get("suggestions", []):
                    if s["id"] == c["suggestion"]:
                        s["check"] = f"range check #{c['id']}: weighed again (it had been dismissed without a compile)"
                        s["status"] = "open" if s["status"] == "dismissed" else s["status"]
        self.net._update_cfg(slug, change)
        if again:
            with self.net.store.tx() as db:
                for c in again:
                    db.execute("DELETE FROM notes WHERE project_id=? AND text LIKE ? AND function_id IN (SELECT id FROM "
                               "functions WHERE project_id=? AND vram=?)",
                               (proj["id"], f"Range check #{c['id']}: %", proj["id"], c["origin"]))
                self.net._event(db, proj["id"], None, "compiler",
                                f"{len(again)} compiler range suggestions dismissed without a compile are weighed again")
            self.recheck(slug)
        return len(again)

    def text(self, proj) -> list[str]:
        """The checks, for compiler_map()."""
        items = self.net.cfg(proj).get("range_checks", [])
        if not items:
            return []
        out = ["Range checks (a suggested range is tested on its neighbours before it applies):"]
        for c in items[-40:]:
            where = ", ".join(f"0x{a:X}-0x{b:X}" for a, b in c["spans"])
            what = "applied range" if c.get("audit") else "suggested"
            out.append(f"- #{c['id']} {what} {where} {self.net._short((c['toolchain'], tuple(c['flags'])))}: "
                       f"{c.get('said') or c['status']}")
        return out
