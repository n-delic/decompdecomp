"""Each helper's next claim, prepared before it asks, so the MCP gateway can hand it over at once.

claim_function is 12% of helpers' calls, and the app answered every one: a helper finishing a function waited for
the busy app to pick its next one and write the receipt. Here the app does that ahead, in the background, for each
helper it started: it picks the function the helper's own claim would get (the same query, filters and model routing
as Network.claim_row, read from a snapshot; no claim is written) and writes its receipt (claim_state.brief). The
routes carry them to the gateway (tool_reader.routes, "prepared"), which gives one to the helper's next plain
claim_function() and then sends the app claim_function(function=<that one>) in the background, so the app records
the claim with every check it makes (gateway/src/main.rs). Two helpers are never prepared the same function; if the
app refuses the recorded claim (someone took the function meanwhile), the helper hears so at the top of its next
reply, with the function the app gave it instead.

Left to the app, which picks then: a helper working on a focus, a function named, a batch (count > 1), a strategy
the helper chose, a fork or room project, a function only a retry grant opens.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

log = logging.getLogger(__name__)

# How often the preparer looks, and how long a prepared claim may be given (its receipt reads the saved candidate).
PREPARE_SECONDS = 2.0
KEEP_SECONDS = 90.0
# What a prepared receipt says in place of the token: the token comes from the claim, recorded after it's given.
PENDING_TOKEN = "claim_token: (being recorded; get_claims() gives it in a moment)"


class Ahead:
    """The prepared claims: helper id -> {slug, function_id, function, text, at}."""

    def __init__(self, net, helpers):
        self.net, self.helpers = net, helpers  # helpers(): {agent_id: slug} of the helpers the app started
        self.prepared: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def routes(self) -> dict:
        with self.lock:
            return {str(agent): dict(entry) for agent, entry in self.prepared.items()}

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="claims-ahead").start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        while not self.stopping.wait(PREPARE_SECONDS):
            try:
                self.tick()
            except Exception:  # the gateway then leaves claims to the app; try again next time
                log.exception("Preparing claims failed")

    def tick(self) -> None:
        helpers = dict(self.helpers())
        with self.lock:
            for agent in [a for a in self.prepared if a not in helpers]:
                del self.prepared[agent]
        for agent_id, slug in helpers.items():
            with self.lock:
                entry = self.prepared.get(agent_id)
            if entry and entry["slug"] == slug and time.time() - entry["at"] < KEEP_SECONDS and self._free(entry):
                continue
            with self.lock:
                taken = {e["function_id"] for a, e in self.prepared.items() if a != agent_id and e["slug"] == slug}
            fresh = prepare(self.net, agent_id, slug, taken)
            with self.lock:
                if fresh is None:
                    self.prepared.pop(agent_id, None)
                else:
                    self.prepared[agent_id] = fresh

    def _free(self, entry) -> bool:
        """Still open, free and as it was when its receipt was written (no new attempt or best C since)."""
        store = self.net.project_store(entry["slug"])
        row = store.one("SELECT attempts,best_score FROM functions WHERE id=? AND status='open' AND id NOT IN "
                        "(SELECT function_id FROM claims WHERE expires_at>?)", entry["function_id"], time.time())
        return row is not None and [row["attempts"], row["best_score"]] == entry["stamp"]


def prepare(net, agent_id: int, slug: str, taken=()) -> dict | None:
    """The claim a plain claim_function() by this helper would get now, with its receipt, or None to leave it to
    the app (see the module docstring)."""
    from .. import claim_stalls
    from ..network import Agent
    from . import automatic_assignment
    from .claim_state import brief
    from .data_regions import blocked as data_blocked
    if agent_id in getattr(net, "_agent_focus", {}) or net.fork_slug(slug) or net._link(slug) is not None:
        return None
    row = net.store.one("SELECT id,name,owner,trusted FROM agents WHERE id=?", agent_id)
    if row is None:
        return None
    agent = Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))
    if automatic_assignment.enabled(net, agent, slug):
        return None
    proj = net._project(slug)
    order = json.loads(proj["config"] or "{}").get("claim_order") or ""
    from ..network import CLAIM_ORDERS
    strategy = order if order in CLAIM_ORDERS else "easiest"
    blocked = sorted(set(claim_stalls.blocked(net, proj, agent)) | set(data_blocked(net, proj)) | set(taken))
    grants = claim_stalls.grants(net, proj, agent)
    store = net.project_store(proj["id"])
    now = time.time()
    plan = net._claim_plan(store, proj, agent, strategy, now, blocked=blocked, retry_grants=grants,
                           sdk=net.sdk_functions(proj), batch_from=None)
    fn = net._claim_pick(lambda sql, args: store._read(sql, args, single=True), *plan)
    if fn is None or fn["id"] in grants or fn["id"] in blocked:
        return None
    text = re.sub(r"(?m)^claim_token: .*$", PENDING_TOKEN, brief(net, fn, agent, "CLAIMED"), count=1)
    return {"slug": slug, "function_id": fn["id"], "function": fn["name"], "text": text, "at": now,
            "stamp": [fn["attempts"], fn["best_score"]]}
