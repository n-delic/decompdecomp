"""What the GUI shows: a project's overview, the function map, a function's detail and diffs, the live feed,
statistics, and the export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ... import compilermap, consoles, scoring, toolchains
from ...network import CLAIM_ORDERS, IN_BUILD, Network, NetworkError, set_aside
from ...project import compiler_for
from .. import saves

# What the notification centre tells: news a person wants to hear about, not every claim and attempt agents make.
NOTIFY_KINDS = ("match", "link", "autopilot", "compiler", "reopen", "not_c", "knowledge", "collab", "import", "reimport", "files",
                "paused", "stopped", "regression")
EVENTS_PER_ASK = 200  # events the notification centre gets per ask (it asks again for more)
# what the project's page gets on each poll (about once a second): the newest since its last ask, at most
LIVE_EVENTS, LIVE_ATTEMPTS = 60, 80
# Rechecking retained source is verification activity, not a new matcher contribution.
MATCHER_ACTIVITY = "COALESCE(a.name, '') NOT IN ('progress-judge', '@progress-judge')"
EVENT_ROWS = ("SELECT e.id, e.kind, e.detail, e.created_at, a.name AS agent, p.slug, p.name AS project FROM events e "
              "JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id "
              f"WHERE {MATCHER_ACTIVITY} AND e.kind IN ({', '.join(repr(k) for k in NOTIFY_KINDS)})")


from ..boundary_repairs import reason as boundary_reason


class GuiMixin:
    def export_rom(self, slug: str, log=print) -> dict:
        """Write a rebuildable source tree (the base for ports and mods) and prove it rebuilds the game."""
        if self.fork_slug(slug):  # a fork's work is already in its source tree, committed
            return {"path": self._project(slug)["root"], "identical": None}

        proj = self._project(slug)
        from .. import variants
        group = variants.game(self, slug)
        export_root = Path(self._project(group['primary_project'])['root'] if group else proj['root'])
        out = export_root / "source"
        def protected(folder):
            return (folder/".git").exists() or (folder/"progress"/"decompdecomp.json").is_file()
        if protected(out):
            # Export regenerates its destination. Never erase a linked repository's
            # progress or curated source; export a separate disposable build tree.
            out = export_root / "source-export"
            if protected(out):
                raise NetworkError("The export destination is a saved repository. Use Update build files to update it safely.")
        if group:
            from ...rom.export_variants import export_game
            res = export_game(self, slug, out, log=log)
        else:
            res = self.console(proj).export_project(self, slug, out, log=log)
        with self.project_store(proj['id']).tx() as db:  # shown on the project page: what's in the tree and whether it rebuilds
            cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
            cfg["export"] = {"t": time.time(), "path": str(res["path"]), "identical": res.get("identical"),
                             "matched": res.get("matched"), "functions": res.get("functions"),
                             "fallback": len(res.get("fallback") or [])}
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        return res

    def overview(self, slug: str, *, room_members=True, quick=False) -> dict:
        from .. import auto_push, match_commits, score_commits
        proj = self._project(slug)
        if quick:
            cfg = self.cfg(proj)
            base = self._overview(slug, proj, room_members=False, quick=True)
            return base | {'overview_pending':True, 'instructions':cfg.get('instructions') or '',
                           'priority':cfg.get('priority',[]), 'spend':cfg.get('spend'),
                           'save_folder':str(saves.save_folder(self, slug)),
                           'suggestions':[], 'boundaries':[], 'compiler_ranges':base.get('compiler_ranges') or self._range_rows(cfg),
                           'auto_commit':match_commits.status(self, proj), 'auto_push':auto_push.status(self, proj), 'score_commits':score_commits.status(self, proj),
                           'auto_compiler':self.auto_adjusts(proj), 'progress_syncing':self.progress.syncing(slug),
                           'progress_sync':self.progress.status(slug)}
        last = self.project_store(proj['id']).one("SELECT MAX(created_at) AS t FROM events WHERE project_id=?", proj["id"])["t"]
        from .. import function_profile_suggestions
        cfg = self.cfg(proj)
        from ..view_snapshots import cache
        revision = self.project_store(proj['id']).one('SELECT map_revision FROM project_totals WHERE project_id=?', proj['id'])
        base = self._overview(slug, proj, room_members=room_members)
        def diagnostics():
            from ..libultra import report
            from ..source_assembly import breakdown
            return {'status_breakdown':breakdown(self,proj), 'libultra':base['libultra'] if 'libultra' in base else report(self,proj),
                    'readable':self.readability.counts(proj)['readable'],
                    'helper_matches':self._helper_match_count(proj)}
        details = diagnostics() if getattr(self.project_store(proj['id'])._local, 'in_tx', False) else cache(self).get(
            ('overview',slug,revision['map_revision'] if revision else proj['config']),diagnostics,ttl=5)
        twins = self.twins.jobs.get(proj["slug"])
        from ..code_coverage import snapshot as coverage_snapshot
        from ..source_preparation import settings as source_settings
        return details | base | {"code_coverage": coverage_snapshot(self, proj), "last_activity": last, "autopilot": self.autopilot_status(slug),
                       "permuter": self.permuter.status(proj["slug"]), "source_preparation": source_settings(self, proj),
                       "twins": None if twins is None else {
                           k: getattr(twins, k) for k in ("state", "progress", "pairs", "tried", "matched", "asm",
                                                        "cross_candidates", "cross_tried", "cross_matched", "cross_job")},
                       "cross_game": self.twins.cross_game_results(slug),
                       "free_search": dict(self.project_store(proj['id']).one('SELECT * FROM reuse_runs WHERE project_id=? ORDER BY id DESC LIMIT 1', proj['id']) or {}),
                       "auto_commit": match_commits.status(self, proj), "auto_push": auto_push.status(self, proj), "score_commits":score_commits.status(self, proj),
                       "progress_syncing": self.progress.syncing(slug),
                       "progress_sync": self.progress.status(slug),
                       "spend": cfg.get("spend"), "instructions": cfg.get("instructions") or "",
                       "claim_order": cfg.get("claim_order") if cfg.get("claim_order") in CLAIM_ORDERS else "",
                       "priority": cfg.get("priority", []), "save_folder": str(saves.save_folder(self, slug)),
                       # (a suggestion its own check disproved isn't one: older ones were filed)
                       "suggestions": [function_profile_suggestions.public(s) for s in cfg.get("suggestions", []) if s["status"] == "open"
                                       and not re.search(r"does not match|doesn't compile", s["check"] or "")
                                       and function_profile_suggestions.actionable(self, proj, s)],
                       "boundaries": [r for r in cfg.get("boundaries", []) if r["status"] == "open"],
                       # (a fork whose files each have a compiler says its own: DS)
                       "compiler_ranges": base.get("compiler_ranges") or self._range_rows(cfg),
                       "auto_compiler": self.auto_adjusts(proj)}

    # The live poll's overview, kept between polls (engine.live). It is rebuilt when one of the project's cheap change
    # counters moves (live_overview_key) or after LIVE_OVERVIEW_TTL; what lives only in memory (autopilot, permuter,
    # twins, saved-progress checks, the room) is read fresh on every poll, as it costs no query.
    LIVE_OVERVIEW_TTL = 10.0
    LIVE_VOLATILE = ('autopilot', 'permuter', 'twins', 'progress_syncing', 'progress_sync', 'room')

    def live_overview_key(self, proj) -> tuple:
        """One indexed read: function and settings changes (map_revision, also bumped by config edits), attempts,
        the newest event and the shared header's version. Anything else the overview shows waits for the TTL."""
        row = self.project_store(proj['id']).one(
            "SELECT (SELECT map_revision FROM project_totals WHERE project_id=?1) AS map, "
            "(SELECT attempts FROM project_totals WHERE project_id=?1) AS attempts, "
            "(SELECT MAX(id) FROM events WHERE project_id=?1) AS event, "
            "(SELECT MAX(version) FROM context_versions WHERE project_id=?1 AND unit='') AS context", proj['id'])
        return (proj['slug'], proj['id'], row['map'], row['attempts'], row['event'], row['context'])

    def live_volatile(self, slug: str, stable: dict) -> dict:
        twins = self.twins.jobs.get(slug)
        out = {"autopilot": self.autopilot_status(slug), "permuter": self.permuter.status(slug),
               "twins": None if twins is None else {
                   k: getattr(twins, k) for k in ("state", "progress", "pairs", "tried", "matched", "asm",
                                                "cross_candidates", "cross_tried", "cross_matched", "cross_job")},
               "progress_syncing": self.progress.syncing(slug), "progress_sync": self.progress.status(slug)}
        if 'room' in stable and not self.fork_slug(slug):
            out['room'] = self._room_info(slug, refresh=False)
        return out

    def live_overview(self, slug: str) -> tuple[dict, str]:
        """The overview for the live poll and a version of it: a digest of its stable part (taken once per rebuild)
        and of the small in-memory part. Unchanged polls neither rebuild nor serialise the whole overview."""
        proj = self._project(slug)
        store = self.project_store(proj['id'])
        state = self.__dict__.setdefault('_live_overviews', {})
        lock = self.__dict__.setdefault('_live_overview_lock', threading.Lock())
        transactional = getattr(store._local, 'in_tx', False)  # (uncommitted rows never go into the cache)
        key = None if transactional else self.live_overview_key(proj)
        now = time.monotonic()
        with lock:
            item = state.get(slug)
        if key is None or item is None or item[0] != key or item[1] <= now:
            full = self.overview(slug, room_members=False)
            stable = {k: v for k, v in full.items() if k not in self.LIVE_VOLATILE}
            digest = hashlib.sha256(json.dumps(stable, separators=(',', ':'), sort_keys=True, default=str).encode()).hexdigest()[:24]
            item = (key, now + self.LIVE_OVERVIEW_TTL, stable, digest)
            if key is not None:
                with lock:
                    state[slug] = item
                    for other in [s for s, v in state.items() if v[1] <= now]:
                        state.pop(other, None)
        stable, digest = item[2], item[3]
        volatile = self.live_volatile(slug, stable)
        moving = hashlib.sha256(json.dumps(volatile, separators=(',', ':'), sort_keys=True, default=str).encode()).hexdigest()[:12]
        return stable | volatile, f'{digest}.{moving}'

    def forget_live_overview(self, slug: str | None = None) -> None:
        """Rebuild the next live overview now (settings saved outside the counters' sight)."""
        state = self.__dict__.get('_live_overviews')
        if state is not None:
            with self.__dict__['_live_overview_lock']:
                state.clear() if slug is None else state.pop(slug, None)

    def _helper_match_count(self, proj):
        ids = [r['id'] for r in self.store.all(
            "SELECT id FROM agents WHERE origin='local' AND name NOT IN ('you','autopilot')")]
        return self.project_store(proj['id']).one(
            "SELECT COUNT(*) n FROM functions WHERE project_id=? AND status='matched' "
            "AND matched_agent_id IN (SELECT value FROM json_each(?))", proj['id'], json.dumps(ids))['n']

    def _overview(self, slug: str, proj, *, room_members=True, quick=False) -> dict:
        if (fork := self.fork_slug(slug)):
            return self.forks.overview(fork, room_members=room_members, quick=quick)
        cfg = self.cfg(proj)
        agg = self.project_store(proj['id']).one("SELECT * FROM project_totals WHERE project_id=?", proj["id"]) or dict.fromkeys(
            ('total','matched','bytes','matched_bytes','set_aside','set_aside_bytes','attempts'), 0)
        # a project made with a compiler decompdecomp doesn't have still opens: the page says so, and its Settings
        # let the user pick one that's here (#118)
        compiler_id = cfg["compiler"]["toolchain"]
        tc = toolchains.get(compiler_id) if toolchains.known(compiler_id) else None
        console = self.console(proj)
        return {
            "slug": proj["slug"], "title": console.display_title(proj["name"], cfg.get("rom", {})), "description": proj["description"],
            "code_audit": self.is_rom(proj) and proj["arch"]=="mips",
            **console.project_facts(),
            "compilers": [c.id for c in console.compilers],
            "rom": {k: cfg["rom"].get(k) for k in ("title", "game_code", "region", "size", "sha1", "byte_order", "compiler_guess")}
                   | {"title": console.display_title(cfg["rom"].get("title", ""), cfg["rom"])},
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
            "context_version": (self.project_store(proj['id']).one("SELECT version FROM context_versions WHERE project_id=? AND unit='' ORDER BY version DESC LIMIT 1", proj["id"]) or {'version':0})['version'],
            "folder": proj["root"],
            "coverage_notes": cfg.get('coverage_notes', []),
            "export": cfg.get("export"),
            "exportable": consoles.EXPORT in console.features,
            "room": self._room_info(proj["slug"], refresh=room_members),
        }

    @contextmanager
    def summary_batch(self):
        local = self.__dict__.setdefault('_summary_local', threading.local())
        previous = getattr(local, 'rows', None)
        query = ("SELECT p.*,t.total,t.matched,t.bytes,t.matched_bytes,t.set_aside,t.set_aside_bytes, "
                 "(SELECT MAX(created_at) FROM events e WHERE e.project_id=p.id) AS last_activity "
                 "FROM projects p LEFT JOIN project_totals t ON t.project_id=p.id")
        catalog = self.store.all(query + ' ORDER BY p.id DESC')
        if all(self.project_store(row['id']) is self.store for row in catalog):
            rows = catalog
        else:
            rows = [self.project_store(row['id']).one(query + ' WHERE p.id=?', row['id']) for row in catalog]
            if any(row is None for row in rows):
                raise RuntimeError('A project database is missing its summary; reopen the project.')
        local.rows = {row['slug']:dict(row) for row in rows}
        try:
            yield rows
        finally:
            local.rows = previous

    def summary(self, slug: str) -> dict:
        """A project for the project list (the sidebar asks every 5 s, for every project): what it is and how far it
        got, from the database alone. It used to be each project's whole overview, a fork's files included, and with
        agents at work the list took 2.5-3.7 s."""
        batch = getattr(getattr(self, '_summary_local', None), 'rows', None)
        proj = batch[slug] if batch is not None and slug in batch else Network._project(self, slug)
        cfg = self.cfg(proj)
        agg = proj if batch is not None and slug in batch else self.project_store(proj['id']).one("SELECT * FROM project_totals WHERE project_id=?", proj["id"]) or dict.fromkeys(
            ('total','matched','bytes','matched_bytes','set_aside','set_aside_bytes'), 0)
        console = self.console(proj)
        if cfg.get("kind") == "fork":
            about = {"kind": "fork", "origin": cfg.get("origin"), "version": cfg.get("version"),
                     "ready": bool(cfg.get("ready")), "rom": {"game_code": cfg.get("version") or "", "region": console.name},
                     "compiler": {"name": "Project's own build"}}
        else:
            about = {"rom": {k: cfg["rom"].get(k) for k in ("game_code", "region")},
                     "compiler": {"name": consoles.compiler_name(cfg.get("compiler", {}).get("toolchain", ""))}}
        return {"slug": proj["slug"], "title": console.display_title(proj["name"], cfg.get("rom", {})), **console.project_facts(),
                "functions": agg["total"] or 0, "matched": agg["matched"] or 0, "bytes": agg["bytes"] or 0,
                "matched_bytes": agg["matched_bytes"] or 0, "set_aside": agg["set_aside"] or 0,
                "set_aside_bytes": agg["set_aside_bytes"] or 0,
                "last_activity": proj['last_activity'] if batch is not None and slug in batch else self.project_store(proj['id']).one(
                    "SELECT MAX(created_at) AS t FROM events WHERE project_id=?", proj['id'])['t'],
                "room": 'guest' if slug in self.links else 'host' if slug in self.rooms else None,
                "autopilot": (self.autopilots.get(slug) or {}).get("state") or ("done" if cfg.get("autopilot") else None),
                "progress_error": self.progress.errors.get(slug)} | about

    def _room_info(self, slug: str, *, refresh=True) -> dict | None:
        if slug in self.links:
            link = self.links[slug]
            status = link.status()
            return {"role": "guest", "host": link.url, "member": link.member, "error": link.error,
                    "members": link.members, "connected": status["state"] in ("syncing", "connected"), **status}
        if slug in self.rooms:
            return {"role": "host", "members": self.rooms[slug].members(refresh=refresh)}
        return None

    def function_map(self, slug: str, *, function_ids=None, quick=False) -> list[dict]:
        """Every function of the project for the map and the list; not one gone from the build (network.IN_BUILD: a
        piece joined to its function, kept for its attempts and notes), which the totals leave out too."""
        proj = self._project(slug)
        subset = " AND f.id IN (SELECT value FROM json_each(?))" if function_ids is not None else ""
        args = (proj['id'], json.dumps(function_ids)) if function_ids is not None else (proj['id'],)
        query = (
            "SELECT f.id, f.name, f.vram, f.size, f.n_insns, f.status, f.best_score, f.attempts, f.unit, f.matched_at, f.meta, "
            "f.readable, "
            "c.agent_id, f.matched_agent_id FROM functions f LEFT JOIN claims c ON c.function_id=f.id "
            f"WHERE f.project_id=? AND f.{IN_BUILD}" + subset + " ORDER BY f.vram, f.unit, f.id")
        store = self.project_store(proj['id'])
        if quick:
            # SQLite releases/reacquires the GIL for each stepped row. With busy
            # background Python workers, thousands of handoffs can dominate this
            # metadata-only read. Aggregate in SQLite and cross the boundary once.
            columns = ('id','name','vram','size','n_insns','status','best_score','attempts',
                       'unit','matched_at','meta','readable','agent_id','matched_agent_id')
            from ..metadata_rows import rows as metadata_rows
            rows = metadata_rows(store, columns, query, *args)
        else:
            rows = store.all(query, *args)
        from ..project_labels import label
        rows = label(self, rows, {'agent':'agent_id', 'matched_by':'matched_agent_id'})
        link = self.links.get(slug)
        # who works on what in the room: by address, or by name in a git project (its functions have no addresses)
        key = (lambda r: r["name"]) if link is not None and link.fork else (lambda r: r["vram"])
        remote = {key(c): "@" + c["agent"] for c in (link.claims if link is not None else [])}
        from ..function_searches import activity
        searching = {r['name']:r['agent'] for r in activity(self,proj)}
        from ..function_names import state as name_state
        from ..history_summary import recency as map_recency
        recency = {} if quick else map_recency(self.project_store(proj['id']), proj['id'], function_ids)
        from ..view_snapshots import cross_project_ids
        cross_project = frozenset() if quick else cross_project_ids(self, proj)
        from ..code_coverage import boundaries
        incomplete = {r["function"]: r["reason"] for r in ([] if quick else boundaries(self, proj)) if not r.get("override")}
        from ..sdk_identity import display_name, identify
        sdk = {} if quick else identify(self,proj,self.sdk_functions(proj),include_asm=True)
        from ..source_assembly import detail as assembly_detail
        proofs={r['id']:assembly_detail(proj,self._function(r['name'],slug)) for r in rows if r['status']=='asm' and 'verified_assembly' in (r['meta'] or '')}
        return [{"id": r["id"], "cross_project": r["id"] in cross_project, **name_state(r), **{k: recency.get(r["id"], {}).get(k) for k in ("last_checked", "last_improved")}, "sdk": sdk.get(r["id"]), "display_name": display_name(sdk.get(r["id"]),r["name"]), "assembly_verification":proofs.get(r["id"]), "name": r["name"], "vram": r["vram"], "size": r["size"], "insns": r["n_insns"], "unit": r["unit"],
                 "status": r["status"] if r["status"] in ("matched", "asm") else "working" if (r["agent"] or key(r) in remote or r["name"] in searching) else "open",
                 "best": r["best_score"], "attempts": r["attempts"], "agent": r["agent"] or remote.get(key(r)) or searching.get(r["name"]),
                 "matched_at": r["matched_at"], "matched_by": r["matched_by"],
                 "analysis_state":"boundary_review" if r["name"] in incomplete else None,
                 "boundary_review":incomplete.get(r["name"]) or (json.loads(r["meta"] or "{}").get("boundary_review") if r["status"]!="matched" else None),
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
        """Constant-time map/layout revisions, changed by committed edits rather than repeated table scans.

        Claims and attempts stay on the incremental live feed; names, metadata, target and build-membership
        edits invalidate the snapshot even when aggregate totals happen to remain identical.
        """
        proj = self._project(slug)
        revision = self.project_store(proj['id']).one("SELECT map_revision FROM project_totals WHERE project_id=?", proj['id'])
        layout = self.project_store(proj['id']).one("SELECT revision FROM layout_revision WHERE id=1")['revision']
        changed = self.project_store(proj['id']).one('SELECT COALESCE(MAX(revision),0) AS n FROM map_changes WHERE project_id=?', proj['id'])['n']
        return f"{proj['id']}.{layout}.{revision['map_revision'] if revision else 'empty'}.{changed}"

    def function_diff(self, slug: str, name: str, which: str = "latest") -> dict:
        """The target next to an attempt, instruction by instruction, for the GUI: the matched C, else the latest
        (or best) attempt, compiled and linked like the judge does. Cached, so watching an agent costs nothing."""
        if (fork := self.fork_slug(slug)):
            return self.forks.function_diff(fork, name)
        fn = self._function(name, slug)
        proj = self.project_store(fn['project_id']).one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        latest = self.project_store(fn['project_id']).one("SELECT source FROM attempts WHERE function_id=? AND (source_hash IS NOT NULL OR source IS NOT NULL) "
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

    def function_preview(self, slug: str, name: str) -> dict:
        """The drawer's first paint reads one function, never generates drafts or inspects whole projects."""
        from ..function_names import state as name_state
        from ..polish_history import detail as polish_detail
        fn = self._function(name, slug)
        proj = self.project_store(fn['project_id']).one('SELECT * FROM projects WHERE id=?', fn['project_id'])
        cfg = self.cfg(proj)
        source = fn['matched_source'] or fn['best_source']
        kind = 'matched' if fn['matched_source'] else 'best' if fn['best_source'] else None
        if not source:
            latest = self.project_store(fn['project_id']).one('SELECT source FROM attempts WHERE function_id=? AND (source_hash IS NOT NULL OR source IS NOT NULL) ORDER BY id DESC LIMIT 1', fn['id'])
            source = latest['source'] if latest else fn['seed']
            kind = 'latest' if latest else 'draft' if fn['seed'] else None
        return {'id':fn['id'], **polish_detail(self.project_store(fn['project_id']),fn,history=False), **name_state(fn), **set_aside(fn), 'name': fn['name'], 'unit': fn['unit'],
                'vram': fn['vram'] or 0, 'rom_offset': fn['rom_offset'] or 0, 'size': fn['size'],
                'insns': fn['n_insns'], 'status': fn['status'], 'best': fn['best_score'],
                'source': source, 'source_kind': kind, 'asm': fn['asm'] or '',
                'scores': [], 'notes': [], 'calls': [], 'called_by': [], 'cross_game_hints': [],
                'compiler_profile': compiler_for(cfg, fn['vram'], fn) if cfg.get('compiler') else None,
                'default_compiler': cfg.get('compiler'), 'details_pending': True}

    def function_detail(self, slug: str, name: str, *, include_hints=True) -> dict:
        from ...match_bundle import status as bundle_status
        from ..baseline_status import status as baseline_status
        from ..draft_reports import read as draft_report
        from ..function_names import state as name_state
        from ..polish_history import detail as polish_detail
        sweep = getattr(self, 'deep_sweeps', None)
        fn = self._function(name, slug)
        deep_pass = sweep.history(slug, fn) if sweep else {'state': 'not tried', 'completed': False}
        proj = self.project_store(fn['project_id']).one('SELECT * FROM projects WHERE id=?', fn['project_id'])
        from ..sdk_identity import display_name, identify
        sdk = identify(self,proj,self.sdk_functions(proj),include_asm=True).get(fn['id'])
        if (fork := self.fork_slug(slug)):
            return self.forks.function_detail(fork, name, include_hints=include_hints, function=fn) | name_state(fn) | polish_detail(self.project_store(fn['project_id']),fn) | {'sdk':sdk, 'display_name':sdk['names'][0] if sdk and len(sdk.get('names',[]))==1 else fn['name'], 'id':fn['id'], 'deep_pass': deep_pass, 'verification': bundle_status(self,fn), 'machine_draft_report': draft_report(self,fn['id'],project_id=fn['project_id'])}
        proj = self.project_store(fn['project_id']).one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        from ..function_history import history
        saved_history = history(self.project_store(fn['project_id']), fn, net=self)
        latest = None if fn['matched_source'] or fn['best_source'] else self.project_store(fn['project_id']).one(
            "SELECT source, score FROM attempts WHERE function_id=? AND (source_hash IS NOT NULL OR source IS NOT NULL) ORDER BY id DESC LIMIT 1", fn['id'])
        x = self.xrefs(proj)
        syms = self.symbols(proj["id"])
        if not (fn["matched_source"] or fn["best_source"] or latest) and fn["status"] == "open":
            self._seed_for(fn, proj)  # (made again first when what it's made from changed)
            fn = self._function(fn["name"], slug)
        from ..source_assembly import detail as assembly_detail
        initial_report = draft_report(self,fn['id'],project_id=fn['project_id'])
        return {
            **polish_detail(self.project_store(fn['project_id']),fn),
            "sdk": sdk, "display_name":display_name(sdk,fn["name"]), "assembly_verification":assembly_detail(proj,fn),
            "machine_draft_report": initial_report,
            **name_state(fn), "id":fn["id"], "name": fn["name"], "vram": fn["vram"], "rom_offset": fn["rom_offset"], "size": fn["size"],
            "insns": fn["n_insns"], "status": fn["status"], "best": fn["best_score"], **set_aside(fn),
            "boundary_review": boundary_reason(self.console(proj),fn),
            "boundary_override": bool(json.loads(fn["meta"] or "{}").get("boundary_override")),
            "deep_pass": deep_pass, "verification": bundle_status(self,fn), "baseline_status": baseline_status(self,proj,fn),
            "compiler_test": self.compiler_test_result(proj, fn),
            "compiler_profile": compiler_for(self.cfg(proj), fn["vram"], fn),
            "default_compiler": self.cfg(proj)["compiler"],
            "compiler_profile_usage": self.compiler_profile_usage(proj),
            "asm": self._asm_text(fn, proj),
            "source": fn["matched_source"] or fn["best_source"] or initial_report.get("compiled_source") or (latest["source"] if latest else None) or fn["seed"],
            "source_kind": "matched" if fn["matched_source"] else "best" if fn["best_source"] else
                           "draft" if initial_report.get("compiled_source") else "latest" if latest else "draft" if fn["seed"] else None,
            **saved_history,
            "calls": [syms.label(a) for a in sorted(x["calls"].get(fn["vram"], ()))],
            "called_by": [syms.label(a) for a in sorted(x["called_by"].get(fn["vram"], ()))],
            "cross_game_hints": self.twins.suggestions(slug, fn["name"]) if include_hints and fn["status"] == "open" else [],
        }

    def stats(self, slug: str) -> dict:
        """Who did what and how fast: the overview's numbers (asked every few seconds, not every second)."""
        pid = self._project(slug)["id"]
        now = time.time()
        from ..history_summary import summary as history_summary
        history = history_summary(self.project_store(pid), pid)['agents']
        names = {r['id']: r['name'] for r in self.store.all('SELECT id,name FROM agents')}
        rows = [{'agent': names[aid], 'compiles': r['compiles'], 'worked': len(r['worked']), 'last': r['last']}
                for aid, r in history.items() if aid in names]
        matched = {names[r['agent_id']]: r['n'] for r in self.project_store(pid).all(
            "SELECT matched_agent_id AS agent_id,COUNT(*) n FROM functions WHERE project_id=? "
            "AND status='matched' GROUP BY matched_agent_id", pid) if r['agent_id'] in names}
        board = [{"agent": r["agent"], "compiles": r["compiles"], "worked": r["worked"], "matched": matched.pop(r["agent"], 0), "last": r["last"]}
                 for r in rows]
        board += [{"agent": a, "compiles": 0, "matched": n, "last": None} for a, n in matched.items()]
        # Only accepted rewrites emit readable events, in ROM and project mode alike.
        polished = {names[r['agent_id']]: r['n'] for r in self.project_store(pid).all(
            "SELECT agent_id,COUNT(DISTINCT detail) n FROM events WHERE project_id=? "
            "AND kind='readable' GROUP BY agent_id", pid) if r['agent_id'] in names}
        for row in board:
            row["polished"] = polished.pop(row["agent"], 0)
        board += [{"agent": a, "compiles": 0, "matched": 0, "polished": n, "last": None}
                  for a, n in polished.items()]
        # Running best before each attempt credits every contributor, not just the final owner.
        # Failed compiles and ties earn nothing; an initial candidate must improve on 0%.
        improved = {names[aid]: len(r['improved']) for aid, r in history.items() if aid in names}
        for row in board:
            row["improved"] = improved.get(row["agent"], 0)
        board.sort(key=lambda r: (-r["matched"], -r["compiles"]))
        per_hour = [0] * 24
        for r in self.project_store(pid).all("SELECT matched_at FROM functions WHERE project_id=? AND status='matched' AND matched_at > ?",
                                pid, now - 86400):
            per_hour[23 - min(23, max(0, int((now - r["matched_at"]) // 3600)))] += 1  # (one matched since `now`: this hour)
        compiles = self.project_store(pid).one("SELECT COUNT(*) AS n FROM attempts t JOIN functions f ON f.id=t.function_id "
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
        identity = self.store.one('SELECT id FROM agents WHERE name=?', agent)
        if identity is None:return {'functions':[], 'more':False}
        agent = identity['id']
        fields='f.id,f.name,f.status,f.best_score,f.n_insns'
        if kind=='worked':
            rows=self.project_store(pid).all('SELECT DISTINCT '+fields+' FROM attempts t JOIN functions f ON f.id=t.function_id '
                                'WHERE f.project_id=? AND t.agent_id=? '
                                'AND t.id>? AND (?=0 OR f.id<?) ORDER BY f.id DESC LIMIT 101',pid,agent,after,before,before)
        elif kind=='matched':
            rows=self.project_store(pid).all('SELECT '+fields+' FROM functions f '
                                "WHERE f.project_id=? AND f.matched_agent_id=? AND f.status='matched' AND (?=0 OR f.id<?) "
                                'AND (?=0 OR EXISTS (SELECT 1 FROM attempts t WHERE t.function_id=f.id AND t.agent_id=f.matched_agent_id '
                                'AND t.compiled AND t.score=0 AND t.id>?)) '
                                'ORDER BY f.id DESC LIMIT 101',pid,agent,before,before,after,after)
        else:
            # Do not filter the worker before the window: every earlier contributor establishes the
            # running best. Ties, failed compiles and 0%-only initial attempts earn no improvement.
            rows=self.project_store(pid).all(
                'WITH history AS (SELECT t.id,t.agent_id,t.function_id,t.score,t.compiled,f.n_insns, '
                'MIN(CASE WHEN t.compiled THEN t.score END) OVER (PARTITION BY t.function_id ORDER BY t.id '
                'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous '
                'FROM attempts t JOIN functions f ON f.id=t.function_id WHERE f.project_id=?) '
                'SELECT DISTINCT '+fields+' FROM history h JOIN functions f ON f.id=h.function_id '
                'WHERE h.agent_id=? AND h.compiled AND h.score>=0 '
                'AND h.score<COALESCE(h.previous,h.n_insns*100) AND h.id>? AND (?=0 OR f.id<?) '
                'ORDER BY f.id DESC LIMIT 101',pid,agent,after,before,before)
        return {'functions':[dict(r) for r in rows[:100]],'more':len(rows)>100}

    def live(self, slug: str, since_event: int = 0, since_attempt: int = 0, *, sections=None) -> dict:
        """The project page's live feed. `sections` (a set of "feed", "claims", "overview"; None: all): what the page
        asked for; a section left out is not read at all, and its key is left out of the answer. `versions` carries the
        overview's version (live_overview) so the page can be sent only what changed."""
        proj = self._project(slug)
        wanted = (lambda part: True) if sections is None else (lambda part: part in sections)
        from ..local_claims import expiry_ids
        from ..project_labels import label
        store = self.project_store(proj['id'])
        hidden = json.dumps([r['id'] for r in self.store.all(
            "SELECT id FROM agents WHERE name IN ('progress-judge','@progress-judge')")])
        # the GUI asks every second: the writer's lock only when a claim really ran out, not on every poll
        if store.one("SELECT 1 FROM claims WHERE expires_at < ? LIMIT 1", time.time()):
            identities = expiry_ids(self, store)
            with store.tx() as db:
                self._expire_claims(db, **identities)
        out = {}
        if wanted('feed'):
            events = label(self, store.all(
                "SELECT id,kind,detail,created_at,agent_id FROM events "
                f"WHERE project_id=? AND id>? AND COALESCE(agent_id,0) NOT IN (SELECT value FROM json_each(?)) "
                f"ORDER BY id DESC LIMIT {LIVE_EVENTS}", proj['id'], since_event, hidden), {'agent':'agent_id'})
            attempts = label(self, store.all(
                "SELECT t.id,t.score,t.score_before,t.score_improved,t.compiled,t.created_at,"
                "f.name AS function,f.n_insns,t.agent_id FROM attempts t JOIN functions f ON f.id=t.function_id "
                "WHERE f.project_id=? AND t.id>? AND COALESCE(t.agent_id,0) NOT IN (SELECT value FROM json_each(?)) "
                f"ORDER BY t.id DESC LIMIT {LIVE_ATTEMPTS}", proj['id'], since_attempt, hidden), {'agent':'agent_id'})
            # (`cut`: more attempts than it sends, so the map's counts need the whole map again)
            out |= {"events": [dict(e) for e in reversed(events)], "attempts": [dict(t) for t in reversed(attempts)],
                    "cut": len(attempts) == LIVE_ATTEMPTS}
        else:
            out |= {"events": [], "attempts": [], "cut": False}
        if wanted('claims'):
            out["claims"] = self._live_claims(slug, proj, store, hidden)
        out["versions"] = {}
        if wanted('overview'):
            out["overview"], out["versions"]["overview"] = self.live_overview(slug)
        return out

    def _live_claims(self, slug, proj, store, hidden) -> list[dict]:
        from ..project_labels import label
        claims = label(self, store.all(
            "SELECT f.name,f.n_insns,f.best_score,f.attempts,c.agent_id,c.claimed_at FROM claims c "
            "JOIN functions f ON f.id=c.function_id WHERE f.project_id=? "
            "AND c.agent_id NOT IN (SELECT value FROM json_each(?))", proj['id'], hidden),
            {'agent':'agent_id'}, require=('agent',))
        if slug in self.links:
            link = self.links[slug]
            key = 'name' if link.fork else 'vram'
            remote = link.claims
            if remote:
                rows = store.all(f"SELECT name,n_insns,best_score,attempts,{key} AS lookup FROM functions "
                                      f"WHERE project_id=? AND {key} IN (SELECT value FROM json_each(?)) ORDER BY id",
                                      proj['id'],json.dumps([c[key] for c in remote]))
                by_key = {}
                for row in rows:
                    by_key.setdefault(row['lookup'], {k:row[k] for k in ('name','n_insns','best_score','attempts')})
                for claim in remote:
                    if row := by_key.get(claim[key]):
                        claims.append(row | {'agent':'@'+claim['agent'],'claimed_at':None})
        from ..function_searches import activity
        occupied = {c['name'] for c in claims}
        claims.extend(row for row in activity(self,proj) if row['name'] not in occupied)
        return claims

    def live_signature(self, slug: str) -> dict:
        """What the live stream (/api/stream) compares between its checks: one token per section of the live feed,
        from the cheapest reads there are (two indexed rows and the claims table, which holds only what agents
        work on now) and from memory. A token that moves says that section has news; nothing here builds it."""
        proj = self._project(slug)
        store = self.project_store(proj['id'])
        row = store.one(
            "SELECT (SELECT map_revision FROM project_totals WHERE project_id=?1) AS map, "
            "(SELECT attempts FROM project_totals WHERE project_id=?1) AS attempts, "
            "(SELECT MAX(id) FROM events WHERE project_id=?1) AS event, "
            "(SELECT MAX(version) FROM context_versions WHERE project_id=?1 AND unit='') AS context, "
            "(SELECT COUNT(*) || ':' || COALESCE(SUM(c.function_id * 1000003 + COALESCE(c.agent_id, 0)), 0) || ':' || "
            " COALESCE(MAX(c.claimed_at), 0) || ':' || COALESCE(SUM(c.expires_at < ?2), 0) "
            " FROM claims c JOIN functions f ON f.id=c.function_id WHERE f.project_id=?1) AS claims", proj['id'], time.time())
        from ..function_searches import active
        link = self.links.get(slug)
        claims = f"{row['claims']}|{active(self, slug)!r}|{link.claims if link else ''!r}"
        volatile = json.dumps(self.live_volatile(slug, {'room': None}), sort_keys=True, default=str)
        return {"feed": f"{row['event']}:{row['attempts']}", "map": row['map'],
                "claims": hashlib.sha256(claims.encode()).hexdigest()[:16],
                "overview": hashlib.sha256(f"{row['map']}|{row['attempts']}|{row['event']}|{row['context']}|{volatile}".encode()).hexdigest()[:16],
                "event": row['event'] or 0}

    def events_after(self, after: int | None, limit: int = EVENTS_PER_ASK) -> dict:
        """The notification centre's feed, for every project at once: what happened after event `after`, oldest
        first. With no `after` (the app's window just opened): nothing, only where the feed starts, so nothing that
        happened before is ever shown as news. Two games with agents at work flooded each project's own feed with
        what happened before it was opened. A range of the events' primary key: cheap however long the history."""
        from .. import activity_index
        if activity_index.selected(self):return activity_index.events_after(self,after,limit)
        last = self.store.one("SELECT COALESCE(MAX(id), 0) AS n FROM events")["n"]
        if after is None:
            return {"last": last, "events": []}
        # (up to `last` only: an event written meanwhile comes with the next ask, not twice)
        rows = self.store.all(f"{EVENT_ROWS} AND e.id>? AND e.id<=? ORDER BY e.id LIMIT ?", after, last, limit)
        return {"last": rows[-1]["id"] if len(rows) == limit else last, "events": [dict(r) for r in rows]}

    def recent_events(self, limit: int = EVENTS_PER_ASK) -> list[dict]:
        """The notification centre's history: the latest notable events of every project, newest first."""
        from .. import activity_index
        if activity_index.selected(self):return activity_index.recent_events(self,limit)
        # Bound each indexed kind before merging, rather than scanning through routine worker events.
        branches = ["SELECT id FROM (SELECT e.id FROM events e INDEXED BY idx_events_kind_id "
                    f"JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id WHERE {MATCHER_ACTIVITY} AND e.kind=? ORDER BY e.id DESC LIMIT ?)"
                    for _ in NOTIFY_KINDS]
        ids = "SELECT id FROM (" + " UNION ALL ".join(branches) + ") ORDER BY id DESC LIMIT ?"
        args = [value for kind in NOTIFY_KINDS for value in (kind,limit)] + [limit]
        return [dict(r) for r in self.store.all(f"{EVENT_ROWS} AND e.id IN ({ids}) ORDER BY e.id DESC", *args)]

    def record_event(self, slug: str, kind: str, detail: str, agent_name: str = "") -> None:
        """Something that happened outside the judge (a helper paused on an error), in the project's feed and the
        notification centre. A project removed meanwhile: nothing to record."""
        proj = self.store.one("SELECT id FROM projects WHERE slug=?", slug)
        agent = self.store.one("SELECT id FROM agents WHERE name=?", agent_name) if agent_name else None
        if proj is not None:
            with self.project_store(proj["id"]).tx() as db:
                self._event(db, proj["id"], agent["id"] if agent else None, kind, detail)
