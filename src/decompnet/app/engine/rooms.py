"""Rooms (collab.py), the engine's side: a project linked to a friend's room forwards claims and attempts to
the host, and the host's events (matches, notes, the header, the layout) arrive here.
"""

from __future__ import annotations

import json
import re
import secrets
import time

from ...network import Network, NetworkError
from ...rom.symbols import Symbol


class RoomMixin:
    def link_project(self, slug: str, info: dict, client=None, start: bool = True):
        store = self.project_store(slug)
        from ..collab import Link

        proj = self._project(slug)
        cfg = json.loads(proj["config"])  # a copy of its own: changed below
        old_link = cfg.get("link") or {}
        same_room = old_link.get("room") == info.get("room") and (
            str(old_link.get("url") or "").rstrip("/") == str(info.get("url") or "").rstrip("/"))
        queued = old_link.get("saved_progress") if same_room else None
        info = dict(info)
        info.pop("saved_progress", None)
        if queued:
            info["saved_progress"] = queued
        cfg["link"] = info
        with store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        link = Link(self, slug, info["url"], info["room"], info["token"], info["member"], client=client)
        self.links[slug] = link
        if start:
            link.run()
        return link

    def restore_links(self) -> None:
        for row in self.store.all("SELECT slug FROM projects"):
            p = self.project_store(row['slug']).one('SELECT slug,config FROM projects WHERE slug=?', row['slug'])
            if p is None:
                raise NetworkError(f"Project {row['slug']!r} is missing from its database. Restore it from backup.")
            info = json.loads(p["config"]).get("link")
            if info and p["slug"] not in self.links:
                self.link_project(p["slug"], info)

    def unlink(self, slug: str) -> None:
        store = self.project_store(slug)
        link = self.links.pop(slug, None)
        if link is not None:
            link.stop()
            try:
                link._req("POST", "/collab/leave", {})
            except NetworkError:
                pass
        proj = self._project(slug)
        cfg = json.loads(proj["config"])  # a copy of its own: changed below
        cfg.pop("link", None)
        with store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))

    def _link(self, project: str | None):
        if not self.links:
            return None
        try:
            return self.links.get(self._project(project)["slug"])
        except NetworkError:
            return None

    def _room_claim_row(self, agent, project, name, strategy):
        """Mirror a host-granted lease, replacing only this worker's stale local room leases.

        Call only after link.claim succeeds. Local expiry and host expiry can differ;
        asking the host again cannot fix a leftover local lease. Roll back if local
        validation fails, and never release another worker's or the host's claim.
        """
        proj = self._project(project)
        project = proj['slug']
        store = self.project_store(proj['id'])
        fn = self._function(name, project)
        self._yield_autopilot(name, project)  # wait outside the transaction for local draft work
        with store.tx() as db:
            stale = db.execute('SELECT f.id,f.name FROM claims c JOIN functions f ON f.id=c.function_id '
                               'WHERE c.agent_id=? AND f.project_id=? AND f.id<>?',
                               (agent.id,proj['id'],fn['id'])).fetchall()
            for row in stale:
                db.execute('DELETE FROM claims WHERE function_id=? AND agent_id=?', (row['id'],agent.id))
            row = Network.claim_row(self, agent, project, name, strategy)
            for old in stale:
                self._event(db,proj['id'],agent.id,'room_claim_resynced',old['name']+' -> '+name)
            return row

    def _remote_agent(self, name: str) -> int:
        label = "@" + name
        if label in self._remote_ids:
            return self._remote_ids[label]
        row = self.store.one("SELECT id FROM agents WHERE name=?", label)
        if row is None:
            with self.store.tx() as db:
                db.execute("INSERT OR IGNORE INTO agents(name,owner,token_hash,created_at,trusted,origin) VALUES(?,?,?,?,1,?)",
                           (label, "room", "remote:" + secrets.token_hex(16), time.time(), "remote"))
                aid = db.execute('SELECT id FROM agents WHERE name=?', (label,)).fetchone()['id']
        else:
            aid = row["id"]
        self._remote_ids[label] = aid
        return aid

    def remote_event(self, project_id: int, kind: str, detail: str, agent_name: str, created_at: float) -> None:
        store = self.project_store(project_id)
        aid = self._remote_agent(agent_name)
        with store.tx() as db:
            db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                       (project_id, aid, kind, detail, created_at))

    def apply_remote_match(self, slug: str, vram: int, source: str, agent_name: str, *, rom_offset=None) -> None:
        store = self.project_store(slug)
        proj = self._project(slug)
        aid = self._remote_agent(agent_name)
        with store.tx() as db:
            rows = db.execute("SELECT id,status,rom_offset FROM functions WHERE project_id=? AND vram=?", (proj["id"], vram)).fetchall()
            row = next((r for r in rows if r['rom_offset']==rom_offset),None) if rom_offset is not None else (rows[0] if len(rows)==1 else None)
            if row is None or row["status"] == "matched":
                return
            db.execute("UPDATE functions SET status='matched', matched_source=?, matched_agent_id=?, matched_at=?, "
                       "best_score=0, best_source=? WHERE id=?", (source, aid, time.time(), source, row["id"]))
            self._release_matched_claim(db,row["id"],aid)

    def apply_remote_match_named(self, slug: str, name: str, agent_name: str) -> None:
        """A git project's room: who matched a function (the code itself arrives with the host's commits)."""
        store = self.project_store(slug)
        proj = self._project(slug)
        aid = self._remote_agent(agent_name)
        with store.tx() as db:
            row = db.execute("SELECT id, status, matched_agent_id FROM functions WHERE project_id=? AND name=?",
                             (proj["id"], name)).fetchone()
            if row is None:
                return
            if row["status"] != "matched":
                db.execute("UPDATE functions SET status='matched', matched_agent_id=?, matched_at=? WHERE id=?",
                           (aid, time.time(), row["id"]))
            elif row["matched_agent_id"] is None:
                db.execute("UPDATE functions SET matched_agent_id=? WHERE id=?", (aid, row["id"]))
            self._release_matched_claim(db,row["id"],aid)

    def apply_remote_note_named(self, slug: str, function: str | None, text: str, agent_name: str, created_at: float, score_before=None, score_after=None) -> None:
        store = self.project_store(slug)
        proj = self._project(slug)
        row = store.one("SELECT id FROM functions WHERE project_id=? AND name=?", proj["id"], function) if function else None
        fid = row["id"] if row else None
        existing = store.one("SELECT id FROM notes WHERE project_id=? AND text=? AND function_id IS ?", proj["id"], text, fid)
        if existing:
            if score_before is not None and score_after is not None:
                with store.tx() as db:
                    db.execute('UPDATE notes SET score_before=?,score_after=? WHERE id=?', (score_before, score_after, existing['id']))
            return
        aid = self._remote_agent(agent_name)
        with store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at,score_before,score_after) VALUES(?,?,?,?,?,?,?)",
                       (proj["id"], fid, aid, text, created_at, score_before, score_after))

    def apply_remote_best(self, slug: str, vram: int, score: int, source: str, *, rom_offset=None) -> None:
        store = self.project_store(slug)
        proj = self._project(slug)
        with store.tx() as db:
            rows=db.execute('SELECT id,rom_offset FROM functions WHERE project_id=? AND vram=?',(proj['id'],vram)).fetchall()
            row=next((r for r in rows if r['rom_offset']==rom_offset),None) if rom_offset is not None else (rows[0] if len(rows)==1 else None)
            if row is not None:
                db.execute("UPDATE functions SET best_score=?,best_source=?,last_improved=CASE WHEN ? < COALESCE(n_insns,0)*100 THEN ? ELSE last_improved END WHERE id=? AND status='open' "
                           "AND (best_score IS NULL OR best_score > ?)",(score,source,score,time.time(),row['id'],score))

    def apply_remote_note(self, slug: str, host_id: int, vram: int | None, text: str, agent_name: str, created_at: float, score_before=None, score_after=None) -> None:
        store = self.project_store(slug)
        proj = self._project(slug)
        fid = None
        if vram is not None:
            row = store.one("SELECT id FROM functions WHERE project_id=? AND vram=?", proj["id"], vram)
            fid = row["id"] if row else None
        existing = store.one("SELECT id FROM notes WHERE project_id=? AND text=? AND function_id IS ?", proj["id"], text, fid)
        if existing:
            if score_before is not None and score_after is not None:
                with store.tx() as db:
                    db.execute('UPDATE notes SET score_before=?,score_after=? WHERE id=?', (score_before, score_after, existing['id']))
            return
        aid = self._remote_agent(agent_name)
        with store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at,score_before,score_after) VALUES(?,?,?,?,?,?,?)",
                       (proj["id"], fid, aid, text, created_at, score_before, score_after))

    def apply_remote_context(self, slug: str, header: str, host_version: int) -> None:
        store = self.project_store(slug)
        proj = self._project(slug)
        if self._context(proj["id"])["header"] == header:
            return
        with store.tx() as db:
            version = self._next_version(db, proj["id"])
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, header, None, f"from the room (host v{host_version})",
                                                  time.time(), ""))

    def apply_remote_layout(self, slug: str, functions: list[dict], symbols: list[dict]) -> None:
        """Mirror the room's splits and names. The host already regression-tested them on the same ROM."""
        store = self.project_store(slug)
        proj = self._project(slug)
        pid = proj["id"]
        console = self.console(proj)
        local = {r["id"]: r for r in store.all("SELECT * FROM functions WHERE project_id=?", pid)}
        by_vram: dict[int, list] = {}
        for row in local.values():
            by_vram.setdefault(row["vram"], []).append(row)
        # Every name a function gave up, as {name it had before this call: name it has now}. The C that says the
        # old names (its own, its callers', the headers) is rewritten once at the end, from the database, so a
        # function renamed twice in one call (stepped aside, then given the host's name) ends up right (#107 review).
        moves: dict[str, str] = {}

        def rename(row, new: str) -> None:
            old = row["name"]
            with store.tx() as db:
                db.execute("UPDATE functions SET name=? WHERE id=?", (new, row["id"]))
            followed = [k for k, v in moves.items() if v == old]
            for k in followed:
                moves[k] = new
            if not followed:
                moves[old] = new
            local[row["id"]] = renamed = {**row, "name": new}
            by_vram[row["vram"]] = [renamed if i["id"] == row["id"] else i for i in by_vram[row["vram"]]]
            self._syms.pop(pid, None)

        def free_name(name: str, keep_id: int | None) -> None:
            """#107: the host's layout is authoritative, so another local function holding `name` steps aside to its
            address's default name (func_XXXXXXXX, _2, _3... when an overlay twin has that) instead of failing the
            unique name. Its symbol follows here, its C at the end."""
            for row in [r for r in local.values() if r["name"] == name and r["id"] != keep_id]:
                base = f"func_{row['vram']:08X}"
                spare, n = base, 2
                while any(r["name"] == spare for r in local.values()) or store.one(
                        "SELECT 1 FROM symbols WHERE project_id=? AND name=?", pid, spare):
                    spare, n = f"{base}_{n}", n + 1
                with store.tx() as db:
                    db.execute("UPDATE symbols SET name=? WHERE project_id=? AND name=?", (spare, pid, name))
                rename(row, spare)

        try:
            self._mirror_layout(proj, console, functions, symbols, local, by_vram, rename, free_name)
        finally:
            self._rename_in_code(pid, moves)

    def _mirror_layout(self, proj, console, functions, symbols, local, by_vram, rename, free_name) -> None:
        pid = proj["id"]
        store = self.project_store(pid)
        for f in sorted(functions, key=lambda f: f["vram"]):
            at = by_vram.get(f["vram"], [])
            if any(row["name"] == f["name"] for row in at):
                continue
            if at:
                # The address belongs to another overlay's function (or a renamed one): never guess which to split.
                continue
            owners = [r for r in local.values() if r["vram"] < f["vram"] < r["vram"] + r["size"]]
            if len(owners) != 1 or owners[0]["status"] == "matched":
                continue
            owner = owners[0]
            try:
                head, tail = console.split_function(owner, f["vram"], f["name"])
            except ValueError:
                continue
            free_name(f["name"], None)
            syms = self.symbols(pid)
            syms.add(Symbol(f["name"], f["vram"], tail.size, "func"))
            th, tt = self._make_target(proj, head, syms), self._make_target(proj, tail, syms)
            with store.tx() as db:
                db.execute("UPDATE functions SET size=?, n_insns=?, target=?, relocs=?, asm=?, seed=NULL WHERE id=?",
                           (head.size, console.function_insns(head), th.code, json.dumps(th.to_json()),
                            console.function_asm(head), owner["id"]))
                meta = console.function_meta(tail)
                tail_id = db.execute(
                    "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                    "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, f["name"], tail.size, console.function_insns(tail), tt.code,
                     json.dumps(tt.to_json()), console.function_asm(tail), None, owner["unit"], f["vram"], tail.rom,
                     console.function_status(tail), json.dumps(meta) if meta else None)).lastrowid
                db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           (pid, f["name"], f["vram"], tail.size, "func"))
            local[owner["id"]] = {**owner, "size": head.size}
            local[tail_id] = {**owner, "id": tail_id, "name": f["name"], "vram": f["vram"], "size": tail.size}
            by_vram.setdefault(f["vram"], []).append(local[tail_id])
        self._xrefs.pop(pid, None)
        for sym in symbols:
            at = by_vram.get(sym["address"], [])
            twins = len(at) > 1
            if twins:
                # Overlays share the address: the host says which of them it named by its unit (layout_symbols). An
                # older host doesn't, and renaming the address would rename all of them.
                at = [row for row in at if sym.get("unit") is not None and row["unit"] == sym["unit"]]
            if len(at) != 1 or at[0]["name"] == sym["name"]:
                continue
            row = at[0]
            free_name(sym["name"], row["id"])
            old = row["name"]
            stale = {old, sym["name"]}
            if not twins:  # the one function's address: whatever symbol names it goes
                current, off = self.symbols(pid).name_at(sym["address"])
                if off == 0:
                    stale.add(current)
            with store.tx() as db:
                db.executemany("DELETE FROM symbols WHERE project_id=? AND name=?", [(pid, n) for n in stale])
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           (pid, sym["name"], sym["address"], sym["size"], sym["kind"]))
            rename(row, sym["name"])

    def _rename_in_code(self, pid: int, moves: dict[str, str]) -> None:
        """Rewrite every matched and best source, and every header, from the names functions had to the names they
        have now: a function that stepped aside and its callers must say its new name, as rename_symbols does, or a
        match is stale and a save writes the wrong C (#107 review)."""
        moves = {old: new for old, new in moves.items() if old != new}
        if not moves:
            return
        word = re.compile(r"\b[A-Za-z_]\w*\b")
        swap = lambda text: word.sub(lambda m: moves.get(m.group(0), m.group(0)), text) if text else text
        with self.project_store(pid).tx() as db:
            for r in db.execute("SELECT id, matched_source, best_source FROM functions WHERE project_id=? AND "
                                "(matched_source IS NOT NULL OR best_source IS NOT NULL)", (pid,)).fetchall():
                matched, best = swap(r["matched_source"]), swap(r["best_source"])
                if (matched, best) != (r["matched_source"], r["best_source"]):
                    db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (matched, best, r["id"]))
            for unit in {""} | self._unit_contexts(pid):
                last = db.execute("SELECT header FROM context_versions WHERE project_id=? AND unit=? "
                                  "ORDER BY version DESC LIMIT 1", (pid, unit)).fetchone()
                if last is None or swap(last["header"]) == last["header"]:
                    continue
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (pid, self._next_version(db, pid), swap(last["header"]), None,
                                                     "names follow the shared layout", time.time(), unit))
