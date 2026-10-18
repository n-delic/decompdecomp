"""Changing the project: names (rename_symbol), the shared header, notes, splitting a function, function-end
reports, and what the user steers (instructions, title, priorities).
"""

from __future__ import annotations

import json
import re
import time

from ... import arch
from ...network import Agent, NetworkError
from ...rom import split
from ...rom.symbols import IDENT, Symbol, SymbolTable
from .common import _ADDRESS


class EditMixin:
    def split_function(self, agent: Agent, project: str | None, function: str, at: str, new_name: str = "") -> str:
        self._rom_only(project, "split_function")
        link = self._link(project)
        if link is not None:
            name = self._function(function, project)["name"]
            text = link.call("/collab/split", agent, function=name, at=at, new_name=new_name)
            if text.startswith("✅"):
                link.sync()
            return text
        fn = self._function(function, project)
        proj = self.store.one("SELECT * FROM projects WHERE id=?", fn["project_id"])
        if not self.is_rom(proj):
            raise NetworkError("split_function is for ROM projects")
        if fn["status"] == "matched":
            raise NetworkError(f"{fn['name']} is matched; it can't be split")
        m = _ADDRESS.match(str(at).strip())
        if not m:
            raise NetworkError("`at` must be an address like 0x80001234")
        addr = int(m.group(1), 16)
        new_name = (new_name or f"func_{addr:08X}").strip()
        syms = self.symbols(proj["id"])
        if not IDENT.match(new_name) or new_name in syms.by_name:
            raise NetworkError(f"`{new_name}` is not a free C identifier")
        head, tail = self._cut(proj, fn, addr, new_name, syms, agent.id, "split", f"{fn['name']} / {new_name}")
        return (f"✅ split: {fn['name']} is now {len(head.lines)} instructions; {new_name} at 0x{addr:08X} has "
                f"{len(tail.lines)}. Continue with {fn['name']} (get_function shows its new assembly).")

    def _cut(self, proj, fn, addr: int, new_name: str, syms: SymbolTable, agent_id: int | None, kind: str, detail: str,
             tail_meta: dict | None = None):
        """Cut a MIPS function in two at `addr`: the head keeps its row, the tail becomes `new_name` (status 'asm'
        with `tail_meta` when it isn't code agents should work on). Returns (head, tail)."""
        rf = self._parse_asm(fn["name"], fn["asm"], fn["unit"])
        try:
            head, tail = split.split_function(rf, addr, new_name)
        except ValueError as e:
            raise NetworkError(str(e)) from None
        syms.add(Symbol(new_name, addr, tail.size, "func"))
        old = syms.by_name.get(fn["name"])
        if old is not None:
            old.size = head.size
        th, tt = self._make_target(proj, head, syms), self._make_target(proj, tail, syms)
        with self.store.tx() as db:
            db.execute("UPDATE functions SET size=?, n_insns=?, target=?, relocs=?, asm=?, seed=NULL, best_score=NULL "
                       "WHERE id=?", (head.size, len(head.lines), th.code, json.dumps(th.to_json()),
                                      self._asm_source(head), fn["id"]))
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                       "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (proj["id"], new_name, tail.size, len(tail.lines), tt.code, json.dumps(tt.to_json()),
                        self._asm_source(tail), None, fn["unit"], addr, tail.rom, "asm" if tail_meta else "open",
                        json.dumps(tail_meta) if tail_meta else None))
            db.execute("UPDATE symbols SET size=? WHERE project_id=? AND name=?", (head.size, proj["id"], fn["name"]))
            db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (proj["id"], new_name, addr, tail.size, "func"))
            self._event(db, proj["id"], agent_id, kind, detail)
        self._xrefs.pop(proj["id"], None)
        return head, tail

    def request_boundary(self, agent: Agent, slug: str, function: str, end: str, reason: str) -> str:
        """An agent's report that a function really ends earlier: what follows is alignment padding, linker leftovers
        or data, not code anyone wrote. Changing the function list is the user's call, so it waits for them on the
        project page (resolve_boundary); approved, the function is cut there and the rest set aside."""
        self._rom_only(slug, "report_boundary")
        if self._link(slug) is not None:
            raise NetworkError("in a team room the host's project decides where functions end: tell the host (add_note)")
        fn = self._function(function, slug)
        proj = self._project(slug)
        if arch.BOUNDARIES not in self.console(proj).features:
            raise NetworkError(f"boundary reports are for MIPS games (N64, PlayStation 2) for now, not {self.console(proj).name}")
        if fn["status"] == "matched":
            raise NetworkError(f"{fn['name']} is matched: its boundaries are proven")
        m = _ADDRESS.match(str(end).strip())
        if not m:
            raise NetworkError("`end` must be an address like 0x80001234: where the function's last instruction ends")
        addr = int(m.group(1), 16)
        off = addr - fn["vram"]
        if off <= 0 or off >= fn["size"] or off % 4:
            raise NetworkError(f"0x{addr:08X} isn't a word inside {fn['name']} (0x{fn['vram']:08X}, {fn['size']} bytes)")
        reason = " ".join(reason.split())[:600]
        if not reason:
            raise NetworkError("say what the bytes after it are (padding, linker leftovers, data) and how you can tell")
        order = "big" if proj["big_endian"] else "little"
        rest = " ".join(f"{int.from_bytes(fn['target'][at:at + 4], order):08X}" for at in range(off, min(fn["size"], off + 32), 4))

        def change(cfg):
            items = cfg.setdefault("boundaries", [])
            items[:] = [r for r in items if not (r["status"] == "open" and r["function"] == fn["name"])]
            items.append({"id": max((r["id"] for r in items), default=0) + 1, "function": fn["name"], "vram": fn["vram"],
                          "end": addr, "cut": fn["size"] - off, "rest": rest, "reason": reason, "agent": agent.name,
                          "t": time.time(), "status": "open"})
        self._update_cfg(slug, change)
        return (f"Reported: {fn['name']} ends at 0x{addr:08X} ({fn['size'] - off} bytes after it set aside if the user "
                f"approves). Until then it stays as it is; release it or work on with its current size.")

    def resolve_boundary(self, slug: str, rid: int, apply: bool) -> None:
        """The user's answer to a boundary report: cut the function there (the rest set aside as not code) or dismiss."""
        proj = self._project(slug)
        req = next((r for r in self.cfg(proj).get("boundaries", []) if r["id"] == rid and r["status"] == "open"), None)
        if req is None:
            raise NetworkError(f"no open boundary report {rid}")
        if apply:
            fn = self._function(req["function"], slug)
            if fn["vram"] != req["vram"] or not 0 < req["end"] - fn["vram"] < fn["size"] or fn["status"] == "matched":
                raise NetworkError(f"{fn['name']} changed since it was reported: dismiss this one")
            name = f"pad_{req['end']:08X}"
            self._cut(proj, fn, req["end"], name, self.symbols(proj["id"]), None, "boundary", f"{fn['name']} ends at 0x{req['end']:08X}",
                      {"asm_code": f"not code (set aside from {fn['name']}): {req['reason']}"})
        self._update_cfg(slug, lambda cfg: next(r for r in cfg["boundaries"] if r["id"] == rid).__setitem__(
            "status", "applied" if apply else "dismissed"))

    def rename_symbol(self, agent: Agent, project: str | None, old: str, new: str, message: str = "") -> str:
        self._rom_only(project, "rename_symbol")
        proj = self._project(project)
        if not self.is_rom(proj):
            return super().rename_symbol(agent, project, old, new, message)
        link = self._link(project)
        if link is not None:
            text = link.call("/collab/rename", agent, old=old, new=new, message=message)
            if text.startswith("✅"):
                link.sync()
            return text
        return self.rename_symbols(agent, project, [(old, new)], message)

    def rename_symbols(self, agent: Agent, project: str | None, pairs: list[tuple[str, str]], message: str = "") -> str:
        """Rename several symbols at once (old -> new): the shared headers, matched sources and best attempts
        change together, after one check that every matched function still matches. One rename is the agents'
        rename_symbol; many at once are the PS2 kernel stubs named at import (consoles/ps2/syscalls.py)."""
        proj = self._project(project)
        pairs = [(old.strip(), new.strip()) for old, new in pairs]
        if not pairs:
            return "nothing to rename"
        syms = self.symbols(proj["id"])
        trial = SymbolTable([Symbol(s.name, s.address, s.size, s.kind) for s in syms.by_name.values()])
        addresses = {}
        for old, new in pairs:
            if not (IDENT.match(old) and IDENT.match(new)):
                raise NetworkError("old and new must be C identifiers")
            addr = syms.resolve(old)
            if addr is None:
                raise NetworkError(f"`{old}` is not a symbol of this ROM: use a name from the assembly (func_/D_...) "
                                   f"or one given earlier")
            taken = trial.resolve(new)
            if taken is not None and taken != addr:
                raise NetworkError(f"`{new}` already names 0x{taken:08X}")
            addresses[old] = addr
            if old in trial.by_name:
                trial.rename(old, new)
            else:
                fnrow = self.store.one("SELECT size FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
                current, _ = syms.name_at(addr)
                base = syms.by_name.get(current) if current != f"D_{addr:08X}" else None
                trial.add(Symbol(new, addr, fnrow["size"] if fnrow else (base.size if base and base.address == addr else 0),
                                 "func" if fnrow else (base.kind if base else "data")))
        new_of = dict(pairs)
        word = re.compile(r"\b(" + "|".join(re.escape(old) for old in new_of) + r")\b")
        swap = lambda text: word.sub(lambda m: new_of[m.group(1)], text)
        units = self._unit_contexts(proj["id"])
        current_ctx = {u: self._context(proj["id"], u) for u in ({""} | units)}
        changed = {u: swap(c["header"]) for u, c in current_ctx.items() if word.search(c["header"])}
        matched = self.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched'", proj["id"])
        rewritten = {r["id"]: swap(r["matched_source"]) for r in matched}
        renamed_rows = [dict(r) | {"name": new_of.get(r["name"], r["name"])} for r in matched]
        header_for = lambda fn: changed.get(self._effective_unit(fn, units),
                                            current_ctx[self._effective_unit(fn, units)]["header"])
        self._sym_override[proj["id"]] = trial
        try:
            broken = self.regression_test(proj, None, sources=rewritten, only=renamed_rows, header_for=header_for)
        finally:
            self._sym_override.pop(proj["id"], None)
        if broken:
            listing = "\n".join(f"- {n}: {why}" for n, why in broken[:10])
            return f"❌ REJECTED: renaming breaks {len(broken)} matched function(s).\n{listing}"
        msg = message.strip() or ", ".join(f"rename {old} -> {new}" for old, new in pairs[:3]) + (" ..." if len(pairs) > 3 else "")
        with self.store.tx() as db:
            for old, new in pairs:
                addr = addresses[old]
                if db.execute("UPDATE symbols SET name=? WHERE project_id=? AND name=?", (new, proj["id"], old)).rowcount == 0:
                    s = trial.by_name[new]
                    db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                               (proj["id"], new, s.address, s.size, s.kind))
                db.execute("UPDATE functions SET name=? WHERE project_id=? AND (name=? OR vram=?)", (new, proj["id"], old, addr))
            for u, header in changed.items():
                version = self._next_version(db, proj["id"])
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, header, agent.id, msg, time.time(), u))
            for fid, src in rewritten.items():
                db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (src, src, fid))
            for r in db.execute("SELECT id, best_source FROM functions WHERE project_id=? AND status='open' "
                                "AND best_source IS NOT NULL", (proj["id"],)).fetchall():
                db.execute("UPDATE functions SET best_source=? WHERE id=?", (swap(r["best_source"]), r["id"]))
            self._event(db, proj["id"], agent.id, "rename", msg if len(pairs) > 1 else f"{pairs[0][0]} → {pairs[0][1]}")
        self._syms.pop(proj["id"], None)
        if len(pairs) == 1:
            (old, new), = pairs
            return (f"✅ 0x{addresses[old]:08X} is now `{new}` (was `{old}`); {len(rewritten)} matched sources updated, "
                    f"all still match.")
        return f"✅ renamed {len(pairs)} symbols; {len(rewritten)} matched sources updated, all still match."

    def edit_context(self, agent: Agent, project: str | None, old: str, new: str, message: str,
                     function: str | None = None) -> str:
        self._rom_only(project, "edit_context")
        link = self._link(project)
        if link is None:
            return super().edit_context(agent, project, old, new, message, function)
        text = link.call("/collab/context", agent, old=old, new=new, message=message, function=function)
        if text.startswith("✅"):
            link.sync()
        return text

    def add_note(self, agent: Agent, project: str | None, text: str, function: str | None = None) -> str:
        link = self._link(project)
        if link is None:
            return super().add_note(agent, project, text, function)
        name = self._function(function, project)["name"] if function else None
        return link.call("/collab/note", agent, text=text, function=name) or "Noted, and shared with the room."

    def set_instructions(self, slug: str, text: str) -> None:
        """What the user wants agents to know or focus on, given to every agent on this project."""
        self._update_cfg(slug, lambda cfg: cfg.__setitem__("instructions", text.strip()[:4000]))

    def instructions(self, slug: str) -> str:
        return self.cfg(self._project(slug)).get("instructions") or ""

    def set_title(self, slug: str, title: str) -> str:
        """The name the project shows by, which the user can change: a disc's title is often only its serial
        (GTA San Andreas imports as SLES-52541). The slug, folders and history keep the name they were made with."""
        title = " ".join(title.split())
        if not title:
            raise NetworkError("give the project a name")
        if len(title) > 80:
            raise NetworkError("keep the name under 80 characters")
        proj = self._project(slug)
        with self.store.tx() as db:
            db.execute("UPDATE projects SET name=? WHERE id=?", (title, proj["id"]))
        return title

    def set_priority(self, slug: str, name: str, on: bool = True) -> list[str]:
        """Functions the user wants done next: automatic claims take these first."""
        fn = self._function(name, slug)

        def change(cfg):
            items = [n for n in cfg.get("priority", []) if n != fn["name"]]
            cfg["priority"] = ([fn["name"]] + items) if on else items
        return self._update_cfg(slug, change)["priority"]
