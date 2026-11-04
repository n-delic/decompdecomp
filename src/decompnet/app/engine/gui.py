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

from ... import agent_notes, compilermap, consoles, scoring, toolchains
from ...network import IN_BUILD, PROGRESS_COLUMNS, Network, set_aside
from ...project import compiler_for

# What the notification centre tells: news a person wants to hear about, not every claim and attempt agents make.
NOTIFY_KINDS = ("match", "link", "autopilot", "compiler", "reopen", "not_c", "knowledge", "collab", "import", "reimport", "files",
                "paused", "stopped", "regression")
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

        proj = self._project(slug)
        res = self.console(proj).export_project(self, slug, Path(proj["root"]) / "source", log=log)
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
        from .. import function_profile_suggestions
        cfg = self.cfg(proj)
        # matches by the helper agents the app runs (their spend is what `spend` counts)
        helpers = self.store.one(
            "SELECT COUNT(*) AS n FROM functions f JOIN agents a ON a.id=f.matched_agent_id WHERE f.project_id=? "
            "AND f.status='matched' AND a.origin='local' AND a.name NOT IN ('you','autopilot')", proj["id"])["n"]
        twins = self.twins.jobs.get(proj["slug"])
        base = self._overview(slug, proj)
        from ..libultra import report as libultra_report
        from ..source_assembly import breakdown
        from ..source_preparation import settings as source_settings
        return base | {"status_breakdown":breakdown(self,proj),"libultra": base["libultra"] if "libultra" in base else libultra_report(self, proj), "last_activity": last, "autopilot": self.autopilot_status(slug),
                       "permuter": self.permuter.status(proj["slug"]), "source_preparation": source_settings(self, proj),
                       "twins": None if twins is None else {
                           k: getattr(twins, k) for k in ("state", "pairs", "tried", "matched", "asm",
                                                        "cross_candidates", "cross_tried", "cross_matched")},
                       "cross_game": self.twins.cross_game_results(slug),
                       "free_search": dict(self.store.one('SELECT * FROM reuse_runs WHERE project_id=? ORDER BY id DESC LIMIT 1', proj['id']) or {}),
                       "helper_matches": helpers, "progress_syncing": self.progress.syncing(slug),
                       "progress_sync": self.progress.status(slug),
                       "readable": self.readability.counts(proj)["readable"],
                       "spend": cfg.get("spend"), "instructions": cfg.get("instructions") or "",
                       "priority": cfg.get("priority", []),
                       # (a suggestion its own check disproved isn't one: older ones were filed)
                       "suggestions": [function_profile_suggestions.public(s) for s in cfg.get("suggestions", []) if s["status"] == "open"
                                       and not re.search(r"does not match|doesn't compile", s["check"] or "")],
                       "boundaries": [r for r in cfg.get("boundaries", []) if r["status"] == "open"],
                       # (a fork whose files each have a compiler says its own: DS)
                       "compiler_ranges": base.get("compiler_ranges") or self._range_rows(cfg),
                       "auto_compiler": self.auto_adjusts(proj)}

    def _overview(self, slug: str, proj) -> dict:
        if (fork := self.fork_slug(slug)):
            return self.forks.overview(fork)
        cfg = self.cfg(proj)
        agg = self.store.one(f"SELECT {PROGRESS_COLUMNS}, SUM(attempts) AS attempts "
                             f"FROM functions WHERE project_id=? AND {IN_BUILD}", proj["id"])
        # a project made with a compiler decompdecomp doesn't have still opens: the page says so, and its Settings
        # let the user pick one that's here (#118)
        compiler_id = cfg["compiler"]["toolchain"]
        tc = toolchains.get(compiler_id) if toolchains.known(compiler_id) else None
        console = self.console(proj)
        return {
            "slug": proj["slug"], "title": proj["name"], "description": proj["description"],
            "code_audit": self.is_rom(proj) and proj["arch"]=="mips",
            **console.project_facts(),
            "compilers": [c.id for c in console.compilers],
            "rom": {k: cfg["rom"].get(k) for k in ("title", "game_code", "region", "size", "sha1", "byte_order", "compiler_guess")},
            "compiler": {"toolchain": compiler_id, "name": consoles.compiler_name(compiler_id),
                         "flags": cfg["compiler"]["flags"],
                         "installed": tc is not None and toolchains.installed(tc.id) is not None,
                         "proprietary": bool(tc and tc.proprietary),
                         "supported": bool(tc and tc.supported), "known": tc is not None,
                         "why": None if tc is None or tc.supported else toolchains.unsupported_cpu(),
                         "missing": [{"id": t, "name": consoles.compiler_name(t), "proprietary": toolchains.get(t).proprietary}
                                     for t in console.installable_compilers() if toolchains.installed(t) is None]},
            "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
            "matched_bytes": agg["matched_bytes"] or 0, "set_aside": agg["set_aside"] or 0,
            "set_aside_bytes": agg["set_aside_bytes"] or 0, "attempts": agg["attempts"] or 0,
            "context_version": self._context(proj["id"])["version"],
            "folder": proj["root"],
            "coverage_notes": cfg.get('coverage_notes', []),
            "export": cfg.get("export"),
            "exportable": consoles.EXPORT in console.features,
            "room": self._room_info(proj["slug"]),
        }

    def summary(self, slug: str) -> dict:
        """A project for the project list (the sidebar asks every 5 s, for every project): what it is and how far it
        got, from the database alone. It used to be each project's whole overview, a fork's files included, and with
        agents at work the list took 2.5-3.7 s."""
        proj = Network._project(self, slug)
        cfg = self.cfg(proj)
        agg = self.store.one(f"SELECT {PROGRESS_COLUMNS} FROM functions WHERE project_id=? AND {IN_BUILD}", proj["id"])
        console = self.console(proj)
        if cfg.get("kind") == "fork":
            about = {"kind": "fork", "origin": cfg.get("origin"), "version": cfg.get("version"),
                     "ready": bool(cfg.get("ready")), "rom": {"game_code": cfg.get("version") or "", "region": console.name},
                     "compiler": {"name": "Project's own build"}}
        else:
            about = {"rom": {k: cfg["rom"].get(k) for k in ("game_code", "region")},
                     "compiler": {"name": consoles.compiler_name(cfg.get("compiler", {}).get("toolchain", ""))}}
        return {"slug": proj["slug"], "title": proj["name"], **console.project_facts(),
                "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
                "matched_bytes": agg["matched_bytes"] or 0, "set_aside": agg["set_aside"] or 0,
                "set_aside_bytes": agg["set_aside_bytes"] or 0,
                "last_activity": self.store.one("SELECT MAX(created_at) AS t FROM events WHERE project_id=?",
                                                proj["id"])["t"],
                "room": (self._room_info(slug) or {}).get("role"),
                "autopilot": (self.autopilots.get(slug) or {}).get("state") or ("done" if cfg.get("autopilot") else None),
                "progress_error": self.progress.errors.get(slug)} | about

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
        """Every function of the project for the map and the list; not one gone from the build (network.IN_BUILD: a
        piece joined to its function, kept for its attempts and notes), which the totals leave out too."""
        proj = self._project(slug)
        rows = self.store.all(
            "SELECT f.id, f.name, f.vram, f.size, f.n_insns, f.status, f.best_score, f.attempts, f.unit, f.matched_at, f.meta, "
            "f.readable, "
            "a.name AS agent, m.name AS matched_by FROM functions f LEFT JOIN claims c ON c.function_id=f.id "
            "LEFT JOIN agents a ON a.id=c.agent_id LEFT JOIN agents m ON m.id=f.matched_agent_id "
            f"WHERE f.project_id=? AND f.{IN_BUILD} ORDER BY f.vram, f.unit, f.id", proj["id"])
        link = self.links.get(slug)
        # who works on what in the room: by address, or by name in a git project (its functions have no addresses)
        key = (lambda r: r["name"]) if link is not None and link.fork else (lambda r: r["vram"])
        remote = {key(c): "@" + c["agent"] for c in (link.claims if link is not None else [])}
        from ..function_names import state as name_state
        recency = {r['function_id']: dict(r) for r in self.store.all(
            'WITH history AS (SELECT t.id,t.function_id,t.compiled,t.score,t.created_at,f.n_insns, '
            'MIN(CASE WHEN t.compiled THEN t.score END) OVER (PARTITION BY t.function_id ORDER BY t.id '
            'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous '
            'FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=?) '
            'SELECT function_id,MAX(created_at) AS last_checked, '
            'MAX(CASE WHEN compiled AND score>=0 AND score<COALESCE(previous,n_insns*100) '
            'THEN created_at END) AS last_improved FROM history GROUP BY function_id', proj['id'])}
        # Exact target bytes, independent of whether either project has matching C.
        # Read only targets, not attempts/source; compare within the same processor and byte order.
        peers = self.store.all(
            f"SELECT f.id,f.project_id,f.target FROM functions f JOIN projects p ON p.id=f.project_id "
            f"WHERE p.arch=? AND p.big_endian=? AND f.{IN_BUILD} AND length(f.target)>0",
            proj['arch'], proj['big_endian'])
        owners = {}
        for peer in peers:
            owners.setdefault(peer['target'], set()).add(peer['project_id'])
        cross_project = {peer['id'] for peer in peers if peer['project_id'] == proj['id']
                         and len(owners[peer['target']]) > 1}
        sdk = self.sdk_functions(proj)
        return [{"cross_project": r["id"] in cross_project, **name_state(r), **{k: recency.get(r["id"], {}).get(k) for k in ("last_checked", "last_improved")}, "sdk": sdk.get(r["id"]), "name": r["name"], "vram": r["vram"], "size": r["size"], "insns": r["n_insns"], "unit": r["unit"],
                 "status": r["status"] if r["status"] in ("matched", "asm") else "working" if (r["agent"] or key(r) in remote) else "open",
                 "best": r["best_score"], "attempts": r["attempts"], "agent": r["agent"] or remote.get(key(r)),
                 "matched_at": r["matched_at"], "matched_by": r["matched_by"],
                 "boundary_review":json.loads(r["meta"] or "{}").get("boundary_review") if r["status"]!="matched" else None,
                 "not_c": r["status"] == "asm" and '"not_c"' in (r["meta"] or ""),
                 "polished": r["status"] == "matched" and bool(r["readable"])} for r in rows]

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
        """What the function map shows, summed up: it changes when a function is added, matched, tried better,
        polished or set aside. A few sums over indexed rows, so the GUI can ask "anything new?" every few seconds
        without fetching tens of thousands of functions (renames and splits come with events, which refetch anyway).
        Not the claims or how many attempts: the live poll brings both every second and the GUI applies them; with them
        here, agents at work changed it every few seconds and a big game's whole map (3 MB) came again each time."""
        proj = self._project(slug)
        f = self.store.one("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS hi, "
                           "COALESCE(SUM(status='matched'), 0) AS m, COALESCE(SUM(best_score), 0) AS bs, "
                           "COALESCE(MAX(matched_at), 0) AS ma, COALESCE(SUM(readable), 0) AS rd, "
                           "COALESCE(SUM(status='asm'), 0) AS sa FROM functions WHERE project_id=?", proj["id"])
        peers = self.store.all("SELECT p.id,COUNT(f.id) AS n,MAX(f.id) AS hi FROM projects p "
                               "LEFT JOIN functions f ON f.project_id=p.id GROUP BY p.id ORDER BY p.id")
        cross_stamp = hashlib.sha1(repr([tuple(r) for r in peers]).encode()).hexdigest()[:12]
        return f"{cross_stamp}.{f['n']}.{f['hi']}.{f['m']}.{f['bs']}.{f['ma']}.{f['rd']}.{f['sa']}"

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
                from ...diff_summary import text as summary_text
                from ...source_lines import mismatches as source_mismatches
                from ..source_diagnostics import hints as source_hints
                from ..stack_layout import text as stack_text
                out = {"rows": [list(r) for r in d.rows], "score": d.score,
                       "source_map": source_mismatches(result.obj, fn["name"], d.rows, source,
                                                       filename="func.cpp" if result.language == "c++" else "func.c"),
                       "stack_diagnostic": stack_text(proj['arch'],d.rows),
                       "mismatch_summary": summary_text(d.rows),
                       "source_diagnostics": source_hints(source, fn["name"], fn["asm"], proj["arch"], d.rows),
                       "percent": 100.0 if d.bytes_match else min(99.9, int(d.percent * 10) / 10),
                       "problems": problems, "source": source,
                       "checks": self._candidate_checks(fn, proj, result.obj),
                       "address_rows": self._candidate_addresses(fn, result.obj)}
        if len(self._diffs) > 128:
            self._diffs.pop(next(iter(self._diffs)))
        self._diffs[key] = out
        return out | {"kind": kind}

    def function_detail(self, slug: str, name: str) -> dict:
        from ...match_bundle import status as bundle_status
        from ..baseline_status import status as baseline_status
        from ..function_names import state as name_state
        sweep = getattr(self, 'deep_sweeps', None)
        deep_pass = sweep.history(slug, self._function(name, slug)) if sweep else {'state': 'not tried', 'completed': False}
        if (fork := self.fork_slug(slug)):
            return self.forks.function_detail(fork, name) | name_state(self._function(name, slug)) | {'deep_pass': deep_pass, 'verification': bundle_status(self,self._function(name,slug))}
        fn = self._function(name, slug)
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        attempts = self.store.all(
            "SELECT t.score, t.created_at, a.name AS agent FROM attempts t LEFT JOIN agents a ON a.id=t.agent_id "
            "WHERE t.function_id=? ORDER BY t.id", fn["id"])
        latest = self.store.one("SELECT source, score FROM attempts WHERE function_id=? AND source IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1", fn["id"])
        from ...search_notes import useful
        notes = self.store.all("SELECT n.*, a.name AS agent FROM notes n LEFT JOIN agents a "
                               "ON a.id=n.agent_id WHERE n.function_id=? "
                               "AND n.text != 'Deep permutation turn ended; improvements shared.' "
                               "ORDER BY n.created_at DESC,n.id DESC", fn["id"])
        x = self.xrefs(proj)
        syms = self.symbols(proj["id"])
        if not (fn["matched_source"] or fn["best_source"] or latest) and fn["status"] == "open":
            self._seed_for(fn, proj)  # (made again first when what it's made from changed)
            fn = self._function(fn["name"], slug)
        from ..source_assembly import detail as assembly_detail
        return {
            "assembly_verification":assembly_detail(proj,fn),
            **name_state(fn), "name": fn["name"], "vram": fn["vram"], "rom_offset": fn["rom_offset"], "size": fn["size"],
            "insns": fn["n_insns"], "status": fn["status"], "best": fn["best_score"], **set_aside(fn),
            "boundary_review": self.console(proj).boundary_reason(fn["target"] or b"") if fn["status"]!="matched" else None,
            "deep_pass": deep_pass, "verification": bundle_status(self,fn), "baseline_status": baseline_status(self,proj,fn),
            "compiler_test": self.compiler_test_result(proj, fn),
            "compiler_profile": compiler_for(self.cfg(proj), fn["vram"]),
            "default_compiler": self.cfg(proj)["compiler"],
            "compiler_profile_usage": self.compiler_profile_usage(proj),
            "asm": self._asm_text(fn, proj),
            "source": fn["matched_source"] or fn["best_source"] or (latest["source"] if latest else None) or fn["seed"],
            "source_kind": "matched" if fn["matched_source"] else "best" if fn["best_source"] else
                           "latest" if latest else "draft" if fn["seed"] else None,
            "scores": [{"score": a["score"], "t": a["created_at"], "agent": a["agent"]} for a in attempts],
            "notes": agent_notes.grouped(self.store, useful(notes), proj["id"])[:FUNCTION_NOTES],
            "calls": [syms.label(a) for a in sorted(x["calls"].get(fn["vram"], ()))],
            "called_by": [syms.label(a) for a in sorted(x["called_by"].get(fn["vram"], ()))],
            "cross_game_hints": self.twins.suggestions(slug, fn["name"]) if fn["status"] == "open" else [],
        }

    def stats(self, slug: str) -> dict:
        """Who did what and how fast: the overview's numbers (asked every few seconds, not every second)."""
        pid = self._project(slug)["id"]
        now = time.time()
        rows = self.store.all(
            "SELECT a.name AS agent, COUNT(t.id) AS compiles, COUNT(DISTINCT t.function_id) AS worked, MAX(t.created_at) AS last FROM attempts t "
            "JOIN functions f ON f.id=t.function_id JOIN agents a ON a.id=t.agent_id WHERE f.project_id=? GROUP BY a.id", pid)
        matched = {r["agent"]: r["n"] for r in self.store.all(
            "SELECT a.name AS agent, COUNT(*) AS n FROM functions f JOIN agents a ON a.id=f.matched_agent_id "
            "WHERE f.project_id=? AND f.status='matched' GROUP BY a.id", pid)}
        board = [{"agent": r["agent"], "compiles": r["compiles"], "worked": r["worked"], "matched": matched.pop(r["agent"], 0), "last": r["last"]}
                 for r in rows]
        board += [{"agent": a, "compiles": 0, "matched": n, "last": None} for a, n in matched.items()]
        # Only accepted rewrites emit readable events, in ROM and project mode alike.
        polished = {r["agent"]: r["n"] for r in self.store.all(
            "SELECT a.name AS agent, COUNT(DISTINCT e.detail) AS n FROM events e "
            "JOIN agents a ON a.id=e.agent_id WHERE e.project_id=? AND e.kind='readable' GROUP BY a.id", pid)}
        for row in board:
            row["polished"] = polished.pop(row["agent"], 0)
        board += [{"agent": a, "compiles": 0, "matched": 0, "polished": n, "last": None}
                  for a, n in polished.items()]
        # Running best before each attempt credits every contributor, not just the final owner.
        # Failed compiles and ties earn nothing; an initial candidate must improve on 0%.
        improved = {r["agent"]: r["n"] for r in self.store.all(
            "WITH history AS (SELECT t.agent_id,t.function_id,t.score,t.compiled,f.n_insns, "
            "MIN(CASE WHEN t.compiled THEN t.score END) OVER (PARTITION BY t.function_id ORDER BY t.id "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous "
            "FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=?) "
            "SELECT a.name AS agent,COUNT(DISTINCT h.function_id) AS n FROM history h "
            "JOIN agents a ON a.id=h.agent_id WHERE h.compiled AND h.score >= 0 "
            "AND h.score < COALESCE(h.previous,h.n_insns * 100) GROUP BY h.agent_id", pid)}
        for row in board:
            row["improved"] = improved.get(row["agent"], 0)
        board.sort(key=lambda r: (-r["matched"], -r["compiles"]))
        per_hour = [0] * 24
        for r in self.store.all("SELECT matched_at FROM functions WHERE project_id=? AND status='matched' AND matched_at > ?",
                                pid, now - 86400):
            per_hour[23 - min(23, max(0, int((now - r["matched_at"]) // 3600)))] += 1  # (one matched since `now`: this hour)
        compiles = self.store.one("SELECT COUNT(*) AS n FROM attempts t JOIN functions f ON f.id=t.function_id "
                                  "WHERE f.project_id=? AND t.created_at > ?", pid, now - 3600)["n"]
        # Agent cards and sorting need every worker, including fresh helpers below the historical leaders.
        # The overview limits its leaderboard in the GUI; truncating here makes active cards show zero.
        return {"agents": board, "matches_per_hour": per_hour, "compiles_last_hour": compiles}

    def agent_results(self, slug, agent, kind='matched', before=0, after=0):
        """Paged functions behind the agent card's counters, using the same attribution as stats."""
        from ...network import NetworkError
        if kind not in ('matched','improved','worked'):
            raise NetworkError('Choose matched, improved or worked functions.')
        pid=self._project(slug)['id']
        before=max(0,int(before))
        after=max(0,int(after))
        fields='f.id,f.name,f.status,f.best_score,f.n_insns'
        if kind=='worked':
            rows=self.store.all('SELECT DISTINCT '+fields+' FROM attempts t JOIN functions f ON f.id=t.function_id '
                                'JOIN agents a ON a.id=t.agent_id WHERE f.project_id=? AND a.name=? '
                                'AND t.id>? AND (?=0 OR f.id<?) ORDER BY f.id DESC LIMIT 101',pid,agent,after,before,before)
        elif kind=='matched':
            rows=self.store.all('SELECT '+fields+' FROM functions f JOIN agents a ON a.id=f.matched_agent_id '
                                "WHERE f.project_id=? AND a.name=? AND f.status='matched' AND (?=0 OR f.id<?) "
                                'AND (?=0 OR EXISTS (SELECT 1 FROM attempts t WHERE t.function_id=f.id AND t.agent_id=a.id '
                                'AND t.compiled AND t.score=0 AND t.id>?)) '
                                'ORDER BY f.id DESC LIMIT 101',pid,agent,before,before,after,after)
        else:
            # Do not filter the worker before the window: every earlier contributor establishes the
            # running best. Ties, failed compiles and 0%-only initial attempts earn no improvement.
            rows=self.store.all(
                'WITH history AS (SELECT t.id,t.agent_id,t.function_id,t.score,t.compiled,f.n_insns, '
                'MIN(CASE WHEN t.compiled THEN t.score END) OVER (PARTITION BY t.function_id ORDER BY t.id '
                'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous '
                'FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=?) '
                'SELECT DISTINCT '+fields+' FROM history h JOIN functions f ON f.id=h.function_id '
                'JOIN agents a ON a.id=h.agent_id WHERE a.name=? AND h.compiled AND h.score>=0 '
                'AND h.score<COALESCE(h.previous,h.n_insns*100) AND h.id>? AND (?=0 OR f.id<?) '
                'ORDER BY f.id DESC LIMIT 101',pid,agent,after,before,before)
        return {'functions':[dict(r) for r in rows[:100]],'more':len(rows)>100}

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
        # (`cut`: more attempts than it sends, so the map's counts need the whole map again)
        return {"events": [dict(e) for e in reversed(events)], "attempts": [dict(t) for t in reversed(attempts)],
                "cut": len(attempts) == LIVE_ATTEMPTS, "claims": claims, "overview": self.overview(slug)}

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
