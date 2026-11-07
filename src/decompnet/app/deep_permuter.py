"""A user-started, bounded search of one saved C candidate.

The ordinary permuter handles near misses in short background turns. This search
lets the user spend a larger explicit budget on one function, while keeping the
same compiler, byte comparison, and best-candidate recording path.
"""

from __future__ import annotations

import time

from ..network import NetworkError
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

    def _hold(self, agent, fn):
        # A manual search must not exhaust the automatic permuter's turn allowance.
        return super()._hold(agent, fn, count_turn=False)

    def _random_seed(self, fn):
        self.turns += 1
        return f"{fn['id']}-deep-{self.turns}"

    def _turn_deadline(self):
        return min(super()._turn_deadline(), self.deadline)

    def _score(self, agent, proj, fn, source, best, fork):
        self.log.check_cancel()
        if time.monotonic() >= self.deadline:
            raise SearchLimit("Time limit reached")
        if self.tested >= self.limit:
            raise SearchLimit("Variant limit reached")
        self.tested += 1
        score = super()._score(agent, proj, fn, source, best, fork)
        if self.tested == 1 or self.tested % 25 == 0:
            self.log(f"Tested {self.tested} candidates; best score {min(best, score) if score is not None else best}.")
        return score


def run(net, slug: str, name: str, minutes: int, max_variants: int, log):
    proj, fn = validate(net, slug, name, minutes, max_variants)
    search = Search(net, minutes, max_variants, log)
    state = State()
    agent, _ = net.local_agent("permuter")
    reason = "Search ended"
    log("Deep permuter is testing this function with the project's compiler and byte comparison.")
    try:
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
    current = net._function(name, slug)
    if current["status"] == "matched":
        reason = "Matched"
    log(f"{reason}: {search.tested} candidates tested; verified improvements were saved immediately.")
    return {"function": name, "reason": reason, "tested": search.tested,
            "matched": current["status"] == "matched", "best_score": current["best_score"]}
