"""What the GUI shows: a project's overview, the function map, a function's detail and diffs, the live feed,
statistics, and the export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

from ... import compilermap, consoles, scoring, toolchains
from ...project import compiler_for

# What the notification centre tells: news a person wants to hear about, not every claim and attempt agents make.
NOTIFY_KINDS = ("match", "link", "autopilot", "compiler", "reopen", "knowledge", "collab", "import", "files",
                "paused", "stopped")
EVENTS_PER_ASK = 200  # events the notification centre gets per ask (it asks again for more)
# what the project's page gets on each poll (about once a second): the newest since its last ask, at most
LIVE_EVENTS, LIVE_ATTEMPTS = 60, 80
FUNCTION_NOTES = 10  # the newest notes the function panel shows
EVENT_ROWS = ("SELECT e.id, e.kind, e.detail, e.created_at, a.name AS agent, p.slug, p.name AS project FROM events e "
              "JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id "
              f"WHERE e.kind IN ({', '.join(repr(k) for k in NOTIFY_KINDS)})")


class GuiMixin:
    def export_rom(self, slug: str, log=print) -> dict:
        """Write a rebuildable source tree (the base for ports and mods) and prove it rebuilds the game."""
        if self.fork_slug(slug):  # a fork's work is already in its source tree, committed
            return {"path": self._project(slug)["root"], "identical": None}
        from ...rom.export import export_rom

        proj = self._project(slug)
        res = export_rom(self, slug, Path(proj["root"]) / "source", self.console(proj), log=log)
        with self.store.tx() as db:  # shown on the project page: what's in the tree and whether it rebuilds
            cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
            cfg["export"] = {"t": time.time(), "path": str(res["path"]), "identical": res.get("identical"),
                             "matched": res.get("matched"), "functions": res.get("functions"),
                             "fallback": len(res.get("fallback") or [])}
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        return res

    def overview(self, slug: str) -> dict:
        proj = self._project(slug)
        last = self.store.one("SELECT MAX(created_at) AS t FROM events WHERE project_id=?", proj["id"])["t"]
        cfg = self.cfg(proj)
        # matches by the helper agents the app runs (their spend is what `spend` counts)
        helpers = self.store.one(
            "SELECT COUNT(*) AS n FROM functions f JOIN agents a ON a.id=f.matched_agent_id WHERE f.project_id=? "
            "AND f.status='matched' AND a.origin='local' AND a.name NOT IN ('you','autopilot')", proj["id"])["n"]
        twins = self.twins.jobs.get(proj["slug"])
        base = self._overview(slug, proj)
        return base | {"last_activity": last, "autopilot": self.autopilot_status(slug),
                       "permuter": self.permuter.status(proj["slug"]),
                       "twins": None if twins is None else {
                           k: getattr(twins, k) for k in ("state", "pairs", "tried", "matched", "asm")},
                       "helper_matches": helpers,
                       "readable": self.readability.counts(proj)["readable"],
                       "spend": cfg.get("spend"), "instructions": cfg.get("instructions") or "",
                       "priority": cfg.get("priority", []),
                       # (a suggestion its own check disproved isn't one: older ones were filed)
                       "suggestions": [s for s in cfg.get("suggestions", []) if s["status"] == "open"
                                       and not re.search(r"does not match|doesn't compile", s["check"] or "")],
                       "boundaries": [r for r in cfg.get("boundaries", []) if r["status"] == "open"],
                       # (a fork whose files each have a compiler says its own: DS)
                       "compiler_ranges": base.get("compiler_ranges") or self._range_rows(cfg),
                       "auto_compiler": self.auto_adjusts(proj)}

    def _overview(self, slug: str, proj) -> dict:
        if (fork := self.fork_slug(slug)):
            return self.forks.overview(fork)
        cfg = self.cfg(proj)
        agg = self.store.one(
            "SELECT COUNT(*) AS total, SUM(status='matched') AS matched, SUM(size) AS bytes, "
            "SUM(CASE WHEN status='matched' THEN size ELSE 0 END) AS matched_bytes, SUM(attempts) AS attempts "
            "FROM functions WHERE project_id=?", proj["id"])
        tc = toolchains.get(cfg["compiler"]["toolchain"])
        console = self.console(proj)
        return {
            "slug": proj["slug"], "title": proj["name"], "description": proj["description"],
            **console.project_facts(),
            "compilers": [c.id for c in console.compilers],
            "rom": {k: cfg["rom"].get(k) for k in ("title", "game_code", "region", "size", "sha1", "byte_order", "compiler_guess")},
            "compiler": {"toolchain": tc.id, "name": consoles.compiler_name(tc.id), "flags": cfg["compiler"]["flags"],
                         "installed": toolchains.installed(tc.id) is not None, "proprietary": tc.proprietary,
                         "supported": tc.supported,
                         "why": None if tc.supported else toolchains.unsupported_cpu()},
            "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
            "matched_bytes": agg["matched_bytes"] or 0, "attempts": agg["attempts"] or 0,
            "context_version": self._context(proj["id"])["version"],
            "folder": proj["root"],
            "export": cfg.get("export"),
            "exportable": consoles.EXPORT in console.features,
            "room": self._room_info(proj["slug"]),
        }

    def summary(self, slug: str) -> dict:
        """A project for the project list (the sidebar asks every 5 s, for every project): what it is and how far it
        got, from the database alone. It used to be each project's whole overview, a fork's files included, and with
        agents at work the list took 2.5-3.7 s."""
        proj = self._project(slug)
        cfg = self.cfg(proj)
        agg = self.store.one(
            "SELECT COUNT(*) AS total, SUM(status='matched') AS matched, SUM(size) AS bytes, "
            "SUM(CASE WHEN status='matched' THEN size ELSE 0 END) AS matched_bytes FROM functions WHERE project_id=?",
            proj["id"])
        console = self.console(proj)
        if cfg.get("kind") == "fork":
            about = {"kind": "fork", "origin": cfg.get("origin"), "version": cfg.get("version"),
                     "ready": bool(cfg.get("ready")), "rom": {"game_code": cfg.get("version") or "", "region": console.name},
                     "compiler": {"name": "Project's own build"}}
        else:
            about = {"rom": {k: cfg["rom"].get(k) for k in ("game_code", "region")},
                     "compiler": {"name": consoles.compiler_name(cfg["compiler"]["toolchain"])}}
        return {"slug": proj["slug"], "title": proj["name"], **console.project_facts(),
                "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
                "matched_bytes": agg["matched_bytes"] or 0,
                "last_activity": self.store.one("SELECT MAX(created_at) AS t FROM events WHERE project_id=?",
                                                proj["id"])["t"],
                "room": (self._room_info(slug) or {}).get("role"),
                "autopilot": (self.autopilot_status(slug) or {}).get("state")} | about

    def _room_info(self, slug: str) -> dict | None:
        if slug in self.links:
            link = self.links[slug]
            status = link.status()
            return {"role": "guest", "host": link.url, "member": link.member, "error": link.error,
                    "members": link.members, "connected": status["state"] in ("syncing", "connected"), **status}
        if slug in self.rooms:
            return {"role": "host", "members": self.rooms[slug].members()}
        return None

    def function_map(self, slug: str) -> list[dict]:
        proj = self._project(slug)
        rows = self.store.all(
            "SELECT f.name, f.vram, f.size, f.n_insns, f.status, f.best_score, f.attempts, f.unit, f.matched_at, "
            "a.name AS agent, m.name AS matched_by FROM functions f LEFT JOIN claims c ON c.function_id=f.id "
            "LEFT JOIN agents a ON a.id=c.agent_id LEFT JOIN agents m ON m.id=f.matched_agent_id "
            "WHERE f.project_id=? ORDER BY f.vram, f.unit, f.id", proj["id"])
        link = self.links.get(slug)
        # who works on what in the room: by address, or by name in a git project (its functions have no addresses)
        key = (lambda r: r["name"]) if link is not None and link.fork else (lambda r: r["vram"])
        remote = {key(c): "@" + c["agent"] for c in (link.claims if link is not None else [])}
        return [{"name": r["name"], "vram": r["vram"], "size": r["size"], "insns": r["n_insns"], "unit": r["unit"],
                 "status": r["status"] if r["status"] in ("matched", "asm") else "working" if (r["agent"] or key(r) in remote) else "open",
                 "best": r["best_score"], "attempts": r["attempts"], "agent": r["agent"] or remote.get(key(r)),
                 "matched_at": r["matched_at"], "matched_by": r["matched_by"]} for r in rows]

    def map_columns(self, slug: str) -> list[list]:
        """The compiler map's view of each function for the ROM map, asked for after the map is drawn: the map no
        longer waits for the compiler map (GTA San Andreas: 710-800 ms after any match). One row per function:
        [vram, the compiler its code looks like, whether that isn't the one it's built with today or a habit rules
        today's out]."""
        proj = self._project(slug)
        try:
            cmap = self.compiler_map(proj, fresh=True)
        except Exception:  # the colours never stand between the user and the ROM map
            logging.getLogger("decompdecomp").exception("the compiler map failed")
            return []
        if cmap is None:
            return []
        cfg = self.cfg(proj)
        out = []
        for addr, looks in cmap.of.items():
            own = compiler_for(cfg, addr)
            tc, small = compilermap.kind(looks)
            out.append([addr, f"{consoles.compiler_name(tc)} {small}".strip(),
                        compilermap.kind(looks) != compilermap.kind((own["toolchain"], tuple(own["flags"])))
                        or addr in cmap.contradicted])
        return out

    def _range_rows(self, cfg: dict) -> list[dict]:
        """The project's compiler ranges for the GUI, each with what checking it again said (rangecheck.py)."""
        said = {tuple(map(tuple, c["spans"])): c.get("said", "") for c in cfg.get("range_checks", []) if c.get("audit")}
        return [r | {"name": consoles.compiler_name(r["toolchain"]), "check": said.get(((r["start"], r["end"]),), "")}
                for r in cfg.get("compiler_ranges", [])]

    def map_signature(self, slug: str) -> str:
        """What the function map shows, summed up: it changes when a function is added, matched, tried or claimed.
        A few sums over indexed rows, so the GUI can ask "anything new?" every few seconds without fetching tens of
        thousands of functions (renames and splits come with events, which refetch anyway)."""
        proj = self._project(slug)
        f = self.store.one("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS hi, COALESCE(SUM(attempts), 0) AS at, "
                           "COALESCE(SUM(status='matched'), 0) AS m, COALESCE(SUM(best_score), 0) AS bs, "
                           "COALESCE(MAX(matched_at), 0) AS ma FROM functions WHERE project_id=?", proj["id"])
        c = self.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(c.function_id * 31 + c.agent_id), 0) AS k FROM claims c "
                           "JOIN functions f ON f.id=c.function_id WHERE f.project_id=?", proj["id"])
        link = self.links.get(slug)
        remote = sorted(str(x.get("vram") or x.get("name")) + x.get("agent", "") for x in (link.claims if link else []))
        return f"{f['n']}.{f['hi']}.{f['at']}.{f['m']}.{f['bs']}.{f['ma']}.{c['n']}.{c['k']}.{hash(tuple(remote)) & 0xFFFFFF}"

    def function_diff(self, slug: str, name: str, which: str = "latest") -> dict:
        """The target next to an attempt, instruction by instruction, for the GUI: the matched C, else the latest
        (or best) attempt, compiled and linked like the judge does. Cached, so watching an agent costs nothing."""
        if (fork := self.fork_slug(slug)):
            return self.forks.function_diff(fork, name)
        fn = self._function(name, slug)
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        latest = self.store.one("SELECT source FROM attempts WHERE function_id=? AND source IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1", fn["id"])
        picks = [("matched", fn["matched_source"]), ("best", fn["best_source"]),
                 ("latest", latest["source"] if latest else None)]
        if which == "latest":
            picks = [picks[0], picks[2], picks[1]]
        kind, source = next(((k, v) for k, v in picks if v), (None, None))
        if source is None:
            return {"kind": None, "rows": None}
        ctx = self._context(proj["id"], fn["unit"])
        key = (fn["id"], hashlib.sha1(source.encode()).hexdigest(), ctx["version"], proj["config"])
        if key in self._diffs:
            return self._diffs[key] | {"kind": kind}
        result = self._compile(proj, ctx["header"], source, fn)
        if not result.ok:
            out = {"rows": None, "error": result.log.strip()[-3000:] or "it didn't compile", "source": source}
        else:
            cand, verdict, problems = self._evaluate(fn, proj, result.obj)
            if cand is None:
                out = {"rows": None, "error": f"the object has no function named `{fn['name']}`", "source": source}
            else:
                d = scoring.diff(self._target_obj(fn, proj), cand)
                if verdict is not None:
                    d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
                out = {"rows": [list(r) for r in d.rows], "score": d.score,
                       "percent": 100.0 if d.bytes_match else min(99.9, int(d.percent * 10) / 10),
                       "problems": problems, "source": source}
        if len(self._diffs) > 128:
            self._diffs.pop(next(iter(self._diffs)))
        self._diffs[key] = out
        return out | {"kind": kind}

    def function_detail(self, slug: str, name: str) -> dict:
        if (fork := self.fork_slug(slug)):
            return self.forks.function_detail(fork, name)
        fn = self._function(name, slug)
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        attempts = self.store.all(
            "SELECT t.score, t.created_at, a.name AS agent FROM attempts t LEFT JOIN agents a ON a.id=t.agent_id "
            "WHERE t.function_id=? ORDER BY t.id", fn["id"])
        latest = self.store.one("SELECT source, score FROM attempts WHERE function_id=? AND source IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1", fn["id"])
        notes = self.store.all("SELECT n.text, n.created_at, a.name AS agent FROM notes n LEFT JOIN agents a "
                               f"ON a.id=n.agent_id WHERE n.function_id=? ORDER BY n.id DESC LIMIT {FUNCTION_NOTES}", fn["id"])
        x = self.xrefs(proj)
        syms = self.symbols(proj["id"])
        if not (fn["matched_source"] or fn["best_source"] or latest) and fn["status"] == "open":
            self._seed_for(fn, proj)  # (made again first when what it's made from changed)
            fn = self._function(fn["name"], slug)
        return {
            "name": fn["name"], "vram": fn["vram"], "rom_offset": fn["rom_offset"], "size": fn["size"],
            "insns": fn["n_insns"], "status": fn["status"], "best": fn["best_score"],
            "asm": self._asm_text(fn, proj),
            "source": fn["matched_source"] or fn["best_source"] or (latest["source"] if latest else None) or fn["seed"],
            "source_kind": "matched" if fn["matched_source"] else "best" if fn["best_source"] else
                           "latest" if latest else "draft" if fn["seed"] else None,
            "scores": [{"score": a["score"], "t": a["created_at"], "agent": a["agent"]} for a in attempts],
            "notes": [dict(n) for n in notes],
            "calls": [syms.label(a) for a in sorted(x["calls"].get(fn["vram"], ()))],
            "called_by": [syms.label(a) for a in sorted(x["called_by"].get(fn["vram"], ()))],
        }

    def stats(self, slug: str) -> dict:
        """Who did what and how fast: the overview's numbers (asked every few seconds, not every second)."""
        pid = self._project(slug)["id"]
        now = time.time()
        rows = self.store.all(
            "SELECT a.name AS agent, COUNT(t.id) AS compiles, MAX(t.created_at) AS last FROM attempts t "
            "JOIN functions f ON f.id=t.function_id JOIN agents a ON a.id=t.agent_id WHERE f.project_id=? GROUP BY a.id", pid)
        matched = {r["agent"]: r["n"] for r in self.store.all(
            "SELECT a.name AS agent, COUNT(*) AS n FROM functions f JOIN agents a ON a.id=f.matched_agent_id "
            "WHERE f.project_id=? AND f.status='matched' GROUP BY a.id", pid)}
        board = [{"agent": r["agent"], "compiles": r["compiles"], "matched": matched.pop(r["agent"], 0), "last": r["last"]}
                 for r in rows]
        board += [{"agent": a, "compiles": 0, "matched": n, "last": None} for a, n in matched.items()]
        board.sort(key=lambda r: (-r["matched"], -r["compiles"]))
        per_hour = [0] * 24
        for r in self.store.all("SELECT matched_at FROM functions WHERE project_id=? AND status='matched' AND matched_at > ?",
                                pid, now - 86400):
            per_hour[23 - min(23, max(0, int((now - r["matched_at"]) // 3600)))] += 1  # (one matched since `now`: this hour)
        compiles = self.store.one("SELECT COUNT(*) AS n FROM attempts t JOIN functions f ON f.id=t.function_id "
                                  "WHERE f.project_id=? AND t.created_at > ?", pid, now - 3600)["n"]
        return {"agents": board[:24], "matches_per_hour": per_hour, "compiles_last_hour": compiles}

    def live(self, slug: str, since_event: int = 0, since_attempt: int = 0) -> dict:
        proj = self._project(slug)
        # the GUI asks every second: the writer's lock only when a claim really ran out, not on every poll
        if self.store.one("SELECT 1 FROM claims WHERE expires_at < ? LIMIT 1", time.time()):
            with self.store.tx() as db:
                self._expire_claims(db)
        events = self.store.all(
            "SELECT e.id, e.kind, e.detail, e.created_at, a.name AS agent FROM events e LEFT JOIN agents a "
            f"ON a.id=e.agent_id WHERE e.project_id=? AND e.id>? ORDER BY e.id DESC LIMIT {LIVE_EVENTS}", proj["id"],
            since_event)
        attempts = self.store.all(
            "SELECT t.id, t.score, t.compiled, t.created_at, f.name AS function, f.n_insns, a.name AS agent "
            "FROM attempts t JOIN functions f ON f.id=t.function_id LEFT JOIN agents a ON a.id=t.agent_id "
            f"WHERE f.project_id=? AND t.id>? ORDER BY t.id DESC LIMIT {LIVE_ATTEMPTS}", proj["id"], since_attempt)
        claims = self.store.all(
            "SELECT f.name, f.n_insns, f.best_score, f.attempts, a.name AS agent, c.claimed_at FROM claims c "
            "JOIN functions f ON f.id=c.function_id JOIN agents a ON a.id=c.agent_id WHERE f.project_id=?", proj["id"])
        claims = [dict(c) for c in claims]
        if slug in self.links:
            for c in self.links[slug].claims:
                row = self.store.one("SELECT name, n_insns, best_score, attempts FROM functions WHERE project_id=? AND vram=?",
                                     proj["id"], c["vram"])
                if row:
                    claims.append(dict(row) | {"agent": "@" + c["agent"], "claimed_at": None})
        return {"events": [dict(e) for e in reversed(events)], "attempts": [dict(t) for t in reversed(attempts)],
                "claims": claims, "overview": self.overview(slug)}

    def events_after(self, after: int | None, limit: int = EVENTS_PER_ASK) -> dict:
        """The notification centre's feed, for every project at once: what happened after event `after`, oldest
        first. With no `after` (the app's window just opened): nothing, only where the feed starts, so nothing that
        happened before is ever shown as news. Two games with agents at work flooded each project's own feed with
        what happened before it was opened. A range of the events' primary key: cheap however long the history."""
        last = self.store.one("SELECT COALESCE(MAX(id), 0) AS n FROM events")["n"]
        if after is None:
            return {"last": last, "events": []}
        # (up to `last` only: an event written meanwhile comes with the next ask, not twice)
        rows = self.store.all(f"{EVENT_ROWS} AND e.id>? AND e.id<=? ORDER BY e.id LIMIT ?", after, last, limit)
        return {"last": rows[-1]["id"] if len(rows) == limit else last, "events": [dict(r) for r in rows]}

    def recent_events(self, limit: int = EVENTS_PER_ASK) -> list[dict]:
        """The notification centre's history: the latest notable events of every project, newest first."""
        return [dict(r) for r in self.store.all(f"{EVENT_ROWS} ORDER BY e.id DESC LIMIT ?", limit)]

    def record_event(self, slug: str, kind: str, detail: str, agent_name: str = "") -> None:
        """Something that happened outside the judge (a helper paused on an error), in the project's feed and the
        notification centre. A project removed meanwhile: nothing to record."""
        proj = self.store.one("SELECT id FROM projects WHERE slug=?", slug)
        agent = self.store.one("SELECT id FROM agents WHERE name=?", agent_name) if agent_name else None
        if proj is not None:
            with self.store.tx() as db:
                self._event(db, proj["id"], agent["id"] if agent else None, kind, detail)
