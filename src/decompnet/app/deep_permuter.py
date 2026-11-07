"""A user-started, bounded search of one saved C candidate.

The ordinary permuter handles near misses in short background turns. This search
lets the user spend a larger explicit budget on one function, while keeping the
same compiler, byte comparison, and best-candidate recording path.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

from ..network import NetworkError
from . import permuter_local
from .permute import Permuter, State


class SearchLimit(Exception):
    pass


def validate(net, slug: str, name: str, minutes: int, max_variants: int):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise NetworkError("Choose a time limit from 1 to 120 minutes.")
    if isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1_000_000:
        raise NetworkError("Choose a variant limit from 1 to 1,000,000.")
    proj = net.project_row(slug)
    fn = net._function(name, slug)
    if slug in net.links:
        raise NetworkError("Run deep permuter on the room host's project.")
    if fn["status"] != "open" or not fn["best_source"] or fn["best_score"] is None:
        raise NetworkError("Choose an unmatched function with saved, scored C first.")
    if net.store.one("SELECT 1 FROM claims WHERE function_id=? AND expires_at>?", fn["id"], time.time()):
        raise NetworkError("This function is working; wait for its claim to finish.")
    return proj, fn


class Search(Permuter):
    def __init__(self, net, minutes: int, max_variants: int, log):
        super().__init__(net)
        self.deadline = time.monotonic() + minutes * 60
        self.limit = max_variants
        self.log = log
        self.tested = 0
        self.turns = 0
        self.seed = time.time_ns()
        self.started = time.monotonic()
        self.starting_score = self.best_score = None
        self.history = []
        self.skipped = self.visited = 0
        self.recent = OrderedDict()
        self.baseline_scope = None
        self.measuring_baseline = False

    def report(self):
        getattr(self.log, "progress", lambda values: None)({
            "tested": self.tested, "skipped": self.skipped, "variant_limit": self.limit,
            "starting_score": self.starting_score, "best_score": self.best_score,
            "elapsed": round(time.monotonic() - self.started),
            "seconds_left": max(0, round(self.deadline - time.monotonic())),
            "history": list(self.history),
        })

    def _hold(self, agent, fn):
        # A manual search must not exhaust the automatic permuter's turn allowance.
        return super()._hold(agent, fn, count_turn=False)

    def _random_seed(self, fn):
        self.turns += 1
        return f"{fn['id']}-deep-{self.seed}-{self.turns}"

    def _turn_deadline(self):
        return min(super()._turn_deadline(), self.deadline)

    def input_scope(self, proj, fn, fork):
        # Imported builds can change headers/build dependencies outside this database.
        # They always go through their build judge; do not reuse their old trials here.
        if fork:
            return None
        fresh_proj = self.net.project_row(proj["slug"])
        fresh_fn = self.net._function(fn["name"], proj["slug"])
        ctx = self.net._context(proj["id"], fresh_fn["unit"])
        if ctx is None:
            raise NetworkError("This project has no compiler header yet. Finish its setup before searching.")
        inputs = (fresh_proj["config"], ctx["header"], ctx["version"],
                  tuple(fresh_fn[k] for k in ("id", "target", "relocs", "vram", "unit", "n_insns")))
        return hashlib.sha256(repr(inputs).encode()).digest()

    def baseline(self, agent, proj, fn, fork):
        self.baseline_scope = self.input_scope(proj, fn, fork)
        self.measuring_baseline = True
        try:
            score = self._score(agent, proj, fn, fn["best_source"], float("inf"), fork)
        finally:
            self.measuring_baseline = False
        if score is None:
            raise NetworkError("The saved C no longer compiles or cannot be judged. Fix it in Edit before searching.")
        if self.input_scope(proj, fn, fork) != self.baseline_scope:
            raise NetworkError("Compiler, header or target changed while checking the baseline. Run the search again.")
        # A header/compiler change can make the saved scalar score obsolete. Rebase
        # only this unchanged source; a concurrent improvement must never be overwritten.
        with self.net.store.tx() as db:
            current = self.net._function(fn["name"], proj["slug"])
            if current["status"] == "open" and current["best_score"] != score:
                if current["best_source"] != fn["best_source"] or current["best_score"] != fn["best_score"]:
                    raise NetworkError("The saved candidate changed while checking its baseline. Run the search again.")
                db.execute("UPDATE functions SET best_score=? WHERE id=?", (score, fn["id"]))
        self.starting_score = self.best_score = score
        self.history = [{"tested": 0, "score": score}]
        self.report()
        return score

    def _score(self, agent, proj, fn, source, best, fork):
        self.log.check_cancel()
        if time.monotonic() >= self.deadline:
            raise SearchLimit("Time limit reached")
        self.visited += 1
        if self.visited > max(100, self.limit * 20):
            raise SearchLimit("No new candidates available")
        scope = self.input_scope(proj, fn, fork)
        if self.baseline_scope is not None and scope != self.baseline_scope:
            raise SearchLimit("Compiler, header or target changed; run the search again")
        key = (scope, hashlib.sha256(source.encode()).digest())
        if not self.measuring_baseline and scope is not None and key in self.recent:
            previous = self.recent[key]
            if previous is None or previous >= best:
                self.skipped += 1
                self.report()
                return previous
        if self.tested >= self.limit:
            raise SearchLimit("Variant limit reached")
        self.tested += 1
        score = super()._score(agent, proj, fn, source, best, fork)
        if self.input_scope(proj, fn, fork) != scope:
            raise SearchLimit("Compiler, header or target changed; run the search again")
        if scope is not None:
            self.recent[key] = score
            self.recent.move_to_end(key)
            if len(self.recent) > 1024:
                self.recent.popitem(last=False)
        if self.measuring_baseline:
            return score
        if self.starting_score is None:
            self.starting_score = self.best_score = best
            self.history = [{"tested": 0, "score": best}]
        if score is not None and score < self.best_score:
            self.best_score = score
            self.history.append({"tested": self.tested, "score": score})
            if len(self.history) > 200:
                self.history = self.history[:1] + self.history[2:-1:2] + self.history[-1:]
        self.report()
        if self.tested == 1 or self.tested % 25 == 0:
            self.log(f"Tested {self.tested} candidates; best score {min(best, score) if score is not None else best}.")
        return score


def run(net, slug: str, name: str, minutes: int, max_variants: int, log):
    proj, fn = validate(net, slug, name, minutes, max_variants)
    search = Search(net, minutes, max_variants, log)
    search.report()
    state = State()
    agent, _ = net.local_agent("permuter")
    reason = "Search ended"
    log("Deep permuter is testing this function with the project's compiler and byte comparison.")
    try:
        if not search._hold(agent, fn):
            raise NetworkError("This function was claimed meanwhile. Try again after it is released.")
        try:
            log("Checking the saved C against the current compiler, header and target.")
            best = search.baseline(agent, proj, fn, net.fork_slug(slug))
            if best > 0:
                current = net._function(name, slug)
                for family, candidate in permuter_local.variants(current["best_source"], name):
                    log.check_cancel()
                    score = search._score(agent, proj, current, candidate, best, net.fork_slug(slug))
                    if score is not None and score < best:
                        best = score
                        log(f"Local rewrite {family} improved the saved candidate.")
                    if score == 0:
                        break
        finally:
            search._let_go(agent, fn)
        while time.monotonic() < search.deadline and search.tested < search.limit:
            log.check_cancel()
            current = net._function(name, slug)
            if current["status"] == "matched":
                reason = "Matched"
                break
            before = state.variants
            search._permute(agent, proj, current, state)
            if state.variants == before:
                reason = "No further variants available"
                break
        else:
            reason = "Variant limit reached" if search.tested >= search.limit else "Time limit reached"
    except SearchLimit as error:
        reason = str(error)
    finally:
        search.report()
    current = net._function(name, slug)
    if current["status"] == "matched":
        reason = "Matched"
    log(f"{reason}: {search.tested} candidates tested; verified improvements were saved immediately.")
    return {"function": name, "reason": reason, "tested": search.tested,
            "matched": current["status"] == "matched", "best_score": current["best_score"]}
