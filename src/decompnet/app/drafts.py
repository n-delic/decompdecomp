"""Machine drafts in the background: every open function of a ROM project gets m2c's draft before anyone opens it,
and gets it again when what it was made from changed (engine/briefs.py, `_draft_plan`: the part of the shared header
it uses, the names in its assembly, its compiler).

Before, a draft was made the first time someone opened a function, once: GTA San Andreas had drafts for 653 of its
12,627 functions, and none of them saw a struct or prototype added to the header after. And each was made inside an
agent's claim, about 0.3 s each, under one lock for the whole app: agents claiming at once waited for each other.

A drafter per project looks for drafts to make (`stale_drafts`) when the project opens or helpers start, then again
whenever the header, the compiler, a name or the game's layout changed (`draft_mark`), every POLL_SECONDS. It makes
them on the background workers every project shares with the permuter (workers.py), at a lower CPU priority that m2c
inherits: agents' compiles and the app come first. A function whose draft changed and that autopilot tried before is tried again by autopilot (the new
draft through the judge), which skips a draft it already judged with the same header.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import wait
from dataclasses import dataclass, field

from ..network import NetworkError
from . import workers

log = logging.getLogger("decompdecomp")
POLL_SECONDS = 30  # how often a drafter looks whether what drafts are made from changed


@dataclass
class State:
    state: str = "running"  # running | stopped | done (the project can't have drafts: not ROM, no m2c)
    total: int = 0  # drafts to make in this pass
    made: int = 0
    retried: int = 0  # changed drafts autopilot judged again
    matched: int = 0
    stop: threading.Event = field(default_factory=threading.Event)


class Drafter:
    def __init__(self, net):
        self.net = net
        self.jobs: dict[str, State] = {}
        self._lock = threading.Lock()

    def status(self, slug: str) -> dict | None:
        st = self.jobs.get(slug)
        return None if st is None else {k: v for k, v in st.__dict__.items() if k != "stop"}

    def start(self, slug: str) -> State:
        with self._lock:
            if (st := self.jobs.get(slug)) is not None and st.state == "running":
                return st
            st = self.jobs[slug] = State()
        threading.Thread(target=self._loop, args=(slug, st), daemon=True, name=f"drafts-{slug}").start()
        return st

    def stop(self, slug: str) -> None:
        if (st := self.jobs.get(slug)) is not None:
            st.stop.set()

    def _loop(self, slug: str, st: State) -> None:
        mark = None
        try:
            while not st.stop.is_set():
                try:
                    now = self.net.draft_mark(self.net.project_row(slug))
                    proj = self.net.project_row(slug)  # (read after the mark: as new as what it sums up)
                except NetworkError:
                    return  # the project is gone
                if not self.net.can_draft(proj):
                    return
                if now != mark:
                    todo = self.net.stale_drafts(proj)
                    st.total, st.made = len(todo), 0
                    if todo:
                        agent, _ = self.net.local_agent("autopilot")
                        wait([workers.pool().submit(self._one, agent, proj, fid, st) for fid in todo])
                    mark = now  # (what changed while the pass ran is seen at the next look)
                st.stop.wait(POLL_SECONDS)
        except Exception:  # background work: it never stands in the way, and says why it stopped
            log.exception("machine drafts for %s stopped", slug)
        finally:
            st.state = "stopped" if st.stop.is_set() else "done"

    def _one(self, agent, proj, fid: int, st: State) -> None:
        if st.stop.is_set():
            return
        try:
            fn = self.net.store.one("SELECT * FROM functions WHERE id=?", fid)
            if fn is None or fn["status"] != "open":
                return
            self.net._seed_for(fn, proj)
            st.made += 1
            if proj["slug"] not in self.net.links and self.net.store.one(
                    "SELECT 1 FROM attempts WHERE function_id=? AND agent_id=?", fid, agent.id):
                # autopilot tried this function's draft before: the new one goes through the judge too (in a room,
                # the host's copy does that, and its matches come back here)
                outcome = self.net._autopilot_one(agent, proj, fid)
                st.retried += outcome in ("match", "near", "far")
                st.matched += outcome == "match"
        except Exception as e:  # one odd function must not stop the rest
            log.info("drafts: %s: %s", fid, e, exc_info=True)
