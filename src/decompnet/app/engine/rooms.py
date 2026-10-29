"""Rooms (collab.py), the engine's side: a project linked to a friend's room forwards claims and attempts to
the host, and the host's events (matches, notes, the header, the layout) arrive here.
"""

from __future__ import annotations

import json
import re
import secrets
import time

from ...network import NetworkError
from ...rom import split
from ...rom.symbols import Symbol


class RoomMixin:
    def link_project(self, slug: str, info: dict, client=None, start: bool = True):
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
        with self.store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        link = Link(self, slug, info["url"], info["room"], info["token"], info["member"], client=client)
        self.links[slug] = link
        if start:
            link.run()
        return link

    def restore_links(self) -> None:
        for p in self.store.all("SELECT slug, config FROM projects"):
            info = json.loads(p["config"]).get("link")
            if info and p["slug"] not in self.links:
                self.link_project(p["slug"], info)

    def unlink(self, slug: str) -> None:
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
        with self.store.tx() as db:
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))

    def _link(self, project: str | None):
        if not self.links:
            return None
        try:
            return self.links.get(self._project(project)["slug"])
        except NetworkError:
            return None

    def _remote_agent(self, name: str) -> int:
        label = "@" + name
        if label in self._remote_ids:
            return self._remote_ids[label]
        row = self.store.one("SELECT id FROM agents WHERE name=?", label)
        if row is None:
            with self.store.tx() as db:
                aid = db.execute("INSERT INTO agents(name,owner,token_hash,created_at,trusted,origin) VALUES(?,?,?,?,1,?)",
                                 (label, "room", "remote:" + secrets.token_hex(16), time.time(), "remote")).lastrowid
        else:
            aid = row["id"]
        self._remote_ids[label] = aid
        return aid

    def remote_event(self, project_id: int, kind: str, detail: str, agent_name: str, created_at: float) -> None:
        aid = self._remote_agent(agent_name)
        with self.store.tx() as db:
            db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                       (project_id, aid, kind, detail, created_at))

    def apply_remote_match(self, slug: str, vram: int, source: str, agent_name: str) -> None:
        proj = self._project(slug)
        aid = self._remote_agent(agent_name)
        with self.store.tx() as db:
            row = db.execute("SELECT id, status FROM functions WHERE project_id=? AND vram=?", (proj["id"], vram)).fetchone()
            if row is None or row["status"] == "matched":
                return
            db.execute("UPDATE functions SET status='matched', matched_source=?, matched_agent_id=?, matched_at=?, "
                       "best_score=0, best_source=? WHERE id=?", (source, aid, time.time(), source, row["id"]))
            db.execute("DELETE FROM claims WHERE function_id=?", (row["id"],))

    def apply_remote_match_named(self, slug: str, name: str, agent_name: str) -> None:
        """A git project's room: who matched a function (the code itself arrives with the host's commits)."""
        proj = self._project(slug)
        aid = self._remote_agent(agent_name)
        with self.store.tx() as db:
            row = db.execute("SELECT id, status, matched_agent_id FROM functions WHERE project_id=? AND name=?",
                             (proj["id"], name)).fetchone()
            if row is None:
                return
            if row["status"] != "matched":
                db.execute("UPDATE functions SET status='matched', matched_agent_id=?, matched_at=? WHERE id=?",
                           (aid, time.time(), row["id"]))
            elif row["matched_agent_id"] is None:
                db.execute("UPDATE functions SET matched_agent_id=? WHERE id=?", (aid, row["id"]))
            db.execute("DELETE FROM claims WHERE function_id=?", (row["id"],))

    def apply_remote_note_named(self, slug: str, function: str | None, text: str, agent_name: str, created_at: float) -> None:
        proj = self._project(slug)
        row = self.store.one("SELECT id FROM functions WHERE project_id=? AND name=?", proj["id"], function) if function else None
        fid = row["id"] if row else None
        if self.store.one("SELECT 1 FROM notes WHERE project_id=? AND text=? AND function_id IS ?", proj["id"], text, fid):
            return
        aid = self._remote_agent(agent_name)
        with self.store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)",
                       (proj["id"], fid, aid, text, created_at))

    def apply_remote_best(self, slug: str, vram: int, score: int, source: str) -> None:
        proj = self._project(slug)
        with self.store.tx() as db:
            db.execute("UPDATE functions SET best_score=?, best_source=? WHERE project_id=? AND vram=? AND status='open' "
                       "AND (best_score IS NULL OR best_score > ?)", (score, source, proj["id"], vram, score))

    def apply_remote_note(self, slug: str, host_id: int, vram: int | None, text: str, agent_name: str, created_at: float) -> None:
        proj = self._project(slug)
        fid = None
        if vram is not None:
            row = self.store.one("SELECT id FROM functions WHERE project_id=? AND vram=?", proj["id"], vram)
            fid = row["id"] if row else None
        if self.store.one("SELECT 1 FROM notes WHERE project_id=? AND text=? AND function_id IS ?", proj["id"], text, fid):
            return
        aid = self._remote_agent(agent_name)
        with self.store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(?,?,?,?,?)",
                       (proj["id"], fid, aid, text, created_at))

    def apply_remote_context(self, slug: str, header: str, host_version: int) -> None:
        proj = self._project(slug)
        if self._context(proj["id"])["header"] == header:
            return
        with self.store.tx() as db:
            version = self._next_version(db, proj["id"])
            db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                       "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, header, None, f"from the room (host v{host_version})",
                                                  time.time(), ""))

    def apply_remote_layout(self, slug: str, functions: list[dict], symbols: list[dict]) -> None:
        """Mirror the room's splits and names. The host already regression-tested them on the same ROM."""
        proj = self._project(slug)
        pid = proj["id"]
        local = {r["id"]: r for r in self.store.all("SELECT * FROM functions WHERE project_id=?", pid)}
        by_vram: dict[int, list] = {}
        for row in local.values():
            by_vram.setdefault(row["vram"], []).append(row)
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
            rf = self._parse_asm(owner["name"], owner["asm"], owner["unit"])
            try:
                head, tail = split.split_function(rf, f["vram"], f["name"])
            except ValueError:
                continue
            syms = self.symbols(pid)
            syms.add(Symbol(f["name"], f["vram"], tail.size, "func"))
            th, tt = self._make_target(proj, head, syms), self._make_target(proj, tail, syms)
            with self.store.tx() as db:
                db.execute("UPDATE functions SET size=?, n_insns=?, target=?, relocs=?, asm=?, seed=NULL WHERE id=?",
                           (head.size, len(head.lines), th.code, json.dumps(th.to_json()), self._asm_source(head), owner["id"]))
                tail_id = db.execute(
                    "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (pid, f["name"], tail.size, len(tail.lines), tt.code,
                                                     json.dumps(tt.to_json()), self._asm_source(tail), None,
                                                     owner["unit"], f["vram"], tail.rom)).lastrowid
                db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           (pid, f["name"], f["vram"], tail.size, "func"))
            local[owner["id"]] = {**owner, "size": head.size}
            local[tail_id] = {**owner, "id": tail_id, "name": f["name"], "vram": f["vram"], "size": tail.size}
            by_vram.setdefault(f["vram"], []).append(local[tail_id])
        self._xrefs.pop(pid, None)
        syms = self.symbols(pid)
        for sym in symbols:
            at = by_vram.get(sym["address"], [])
            if any(row["name"] == sym["name"] for row in at):
                continue
            if len(at) != 1:
                # An overlay address names several functions; renaming the address would rename all of them.
                continue
            row = at[0]
            current, off = syms.name_at(sym["address"])
            if off == 0 and current == sym["name"]:
                continue
            old = current if off == 0 else f"D_{sym['address']:08X}"
            word = re.compile(rf"\b{re.escape(old)}\b")
            with self.store.tx() as db:
                db.execute("DELETE FROM symbols WHERE project_id=? AND (name=? OR name=?)", (pid, old, sym["name"]))
                db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                           (pid, sym["name"], sym["address"], sym["size"], sym["kind"]))
                db.execute("UPDATE functions SET name=?, matched_source=?, best_source=? WHERE id=?",
                           (sym["name"],
                            word.sub(sym["name"], row["matched_source"]) if row["matched_source"] else None,
                            word.sub(sym["name"], row["best_source"]) if row["best_source"] else None, row["id"]))
            renamed = {**row, "name": sym["name"]}
            local[row["id"]] = renamed
            by_vram[sym["address"]] = [renamed if item["id"] == row["id"] else item
                                       for item in by_vram[sym["address"]]]
            self._syms.pop(pid, None)
            syms = self.symbols(pid)
