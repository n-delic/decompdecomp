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

Once a ROM project's drafts are made, the drafter judges each open function's drafts and saved C under the other
profiles its matched functions use (profile_drafts.py), on a low-priority worker group of its own with its own Stop:
again when the drafts' inputs or those profiles change, never for a function already finished.

A PC game started from scratch gets Ghidra's drafts the same way (forkengine: can_draft, stale_drafts, draft_mark,
background_draft), a source file at a time; what follows its headers there is the draft made compilable from them
(consoles/pc/drafts.py), so a header that changed sends autopilot back to the functions it tried.
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
DRAFT_BATCH = 2  # initial compiles share SQLite/progress writes; do not flood the global worker pool
SYNC_WAIT = 5  # how often it looks whether the room it joined has caught up
PROFILE_WIDTH = 2  # functions judged at once under other profiles (profile_drafts.py): it waits behind agents' compiles


@dataclass
class State:
    state: str = "running"  # running | stopped | done (the project can't have drafts: no m2c, or no Ghidra on PC)
    total: int = 0  # drafts to make in this pass
    made: int = 0
    retried: int = 0  # changed drafts autopilot judged again
    matched: int = 0
    # drafts under other profiles (profile_drafts.py), after the drafts: functions looked at, matched, waiting on the
    # project page
    profiles_total: int = 0
    profiles_done: int = 0
    profiles_matched: int = 0
    profiles_waiting: int = 0
    current: str = ""  # what Background work shows beside the drafter
    stop: threading.Event = field(default_factory=threading.Event)
    profiles_stop: threading.Event = field(default_factory=threading.Event)  # its own Stop: the drafts go on


class Drafter:
    def __init__(self, net):
        self.net = net
        self.jobs: dict[str, State] = {}
        self._lock = threading.Lock()
        self._profiles_retry: dict[str, set[int]] = {}  # functions another worker held during the last such pass

    def status(self, slug: str) -> dict | None:
        st = self.jobs.get(slug)
        return None if st is None else {k: v for k, v in st.__dict__.items() if not isinstance(v, threading.Event)}

    def start(self, slug: str) -> State:
        with self._lock:
            if (st := self.jobs.get(slug)) is not None and st.state in ("running", "update_wait", "idle"):
                return st
            st = self.jobs[slug] = State()
        threading.Thread(target=self._loop, args=(slug, st), daemon=True, name=f"drafts-{slug}").start()
        return st

    def stop(self, slug: str) -> None:
        if (st := self.jobs.get(slug)) is not None:
            st.stop.set()

    def _loop(self, slug: str, st: State) -> None:
        mark = profiles = None
        try:
            while not st.stop.is_set():
                from .project_activity import active
                if not active(self.net, slug):
                    st.state = "idle"
                    st.stop.wait(2)
                    continue
                st.state = "running"
                from .source_runtime import checkpoint, idle_wait
                checkpoint(self.net, st)
                if st.stop.is_set():
                    return
                link = self.net.links.get(slug)
                if link is not None and link.status()["state"] == "syncing":
                    # a room's first sync is still bringing the names and matches that drafts are made from: drafting
                    # meanwhile fought it for the database (its names went 5 times slower) and every draft was made again
                    st.stop.wait(min(POLL_SECONDS, SYNC_WAIT))
                    continue
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
                        from .purpose_work import Executor
                        with Executor(workers.WORKERS, purpose='Machine drafts', project=slug, stop=st.stop.set, initializer=workers.lower_priority) as pool:
                            for start in range(0,len(todo),DRAFT_BATCH):
                                if st.stop.is_set() or not active(self.net, slug):break
                                wait([pool.submit(self._one, agent, proj, fid, st) for fid in todo[start:start+DRAFT_BATCH]])
                    mark = now if active(self.net, slug) else None  # (what changed while the pass ran is seen at the next look)
                if mark == now:  # (the drafts are made: their pass under the other profiles comes after them)
                    profiles = self._other_profiles(slug, st, now, profiles)
                idle_wait(self.net, st, POLL_SECONDS)
        except Exception:  # background work: it never stands in the way, and says why it stopped
            log.exception("machine drafts for %s stopped", slug)
        finally:
            st.state = "stopped" if st.stop.is_set() else "done"

    def _other_profiles(self, slug: str, st: State, mark, last):
        """Drafts under other profiles (profile_drafts.py), once the drafts are made, over every open function: again
        when what drafts are made from or the profiles the matched functions use changed, and for the functions another
        worker held, at the next look. Returns what the finished pass was over (None: not finished, look again)."""
        from . import profile_drafts
        from .project_activity import active
        try:
            proj = self.net.project_row(slug)
        except NetworkError:
            return last
        if st.profiles_stop.is_set() or not profile_drafts.supported(self.net, proj):
            return last
        now = (mark, profile_drafts.look(self.net, proj))
        retry = self._profiles_retry.get(slug)
        if now == last and not retry:
            return last
        todo = profile_drafts.open_functions(self.net, proj) if now != last else [f for f in profile_drafts.open_functions(
            self.net, proj) if f in retry]
        st.profiles_total, st.profiles_done = len(todo), 0
        used = profile_drafts.used_profiles(self.net, proj)
        agent, _ = self.net.local_agent("autopilot")
        busy: set[int] = set()

        def cancelled():
            return st.stop.is_set() or st.profiles_stop.is_set() or not active(self.net, slug)

        def one(fid):
            if cancelled():
                return
            try:
                fn = self.net.project_store(proj["id"]).one("SELECT * FROM functions WHERE id=?", fid)
                outcome = "skip" if fn is None else profile_drafts.run_one(self.net, agent, proj, fn, used, cancelled)
            except Exception:  # one odd function must not stop the rest
                log.info("drafts under other profiles: %s", fid, exc_info=True)
                return
            st.profiles_done += 1
            st.profiles_matched += outcome == "matched"
            st.profiles_waiting += outcome == "waiting"
            if outcome == "busy":
                busy.add(fid)
        from .purpose_work import Executor
        try:
            with Executor(min(PROFILE_WIDTH, workers.WORKERS), purpose="Drafts under other profiles", project=slug,
                          stop=st.profiles_stop.set, initializer=workers.lower_priority) as pool:
                for start in range(0, len(todo), PROFILE_WIDTH):
                    if cancelled():
                        break
                    st.current = (f"Drafts under other profiles: {st.profiles_done:,} of {len(todo):,} functions, "
                                  f"{st.profiles_matched:,} matched")
                    wait([pool.submit(one, fid) for fid in todo[start:start + PROFILE_WIDTH]])
        finally:
            st.current = ""
        if cancelled():
            return None if not st.profiles_stop.is_set() else last  # (idle: the rest when it is worked on again)
        self._profiles_retry[slug] = busy
        return now

    def _one(self, agent, proj, fid: int, st: State) -> None:
        from .project_activity import active
        if st.stop.is_set() or not active(self.net, proj['slug']):
            return
        try:
            fn = self.net.project_store(proj['id']).one("SELECT * FROM functions WHERE id=?", fid)
            if fn is None or fn["status"] != "open":
                return
            from .boundary_repairs import blocked
            if fid in blocked(self.net,proj):return
            fork = self.net.fork_slug(proj["slug"])  # a game started from scratch in project mode (PC)
            if fork:
                self.net.forks.background_draft(proj, fn)
                recovered = not fn["best_source"]
            else:
                self.net._seed_for(fn, proj)
                recovered = not fn['best_source']
            st.made += 1
            if proj["slug"] not in self.net.links and (recovered or self.net.project_store(proj['id']).one(
                    "SELECT 1 FROM attempts WHERE function_id=? AND agent_id=?", fid, agent.id)):
                # autopilot tried this function's draft before: the new one goes through the judge too (in a room,
                # the host's copy does that, and its matches come back here)
                # A fresh or recovered seed has no attempt until its first compilation.
                outcome = (self.net.forks.autopilot_one if fork else self.net._autopilot_one)(agent, proj, fid)
                st.retried += outcome in ("match", "near", "far")
                st.matched += outcome == "match"
        except Exception as e:  # one odd function must not stop the rest
            log.info("drafts: %s: %s", fid, e, exc_info=True)
