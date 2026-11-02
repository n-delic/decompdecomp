"""Changing the project: names (rename_symbol), the shared header, notes, splitting a function, function-end
reports, and what the user steers (instructions, title, priorities).
"""

from __future__ import annotations

import json
import re
import time

from ... import arch
from ...network import CLAIM_ORDERS, NOT_C_AGENTS, NOT_C_MODELS, Agent, NetworkError, model_of
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
        console = self.console(proj)
        inside = fn["vram"] <= addr < fn["vram"] + fn["size"]
        if not inside and addr % console.instruction_size(fn) == 0:
            rom, _ = self._rom(proj)
            following = [r["vram"] for r in self.store.all(
                "SELECT vram FROM functions WHERE project_id=? AND vram>?", proj["id"], addr)]
            limit = min(following) if following else fn["vram"] - (fn["rom_offset"] or 0) + len(rom)
            new = console.function_at(rom, addr, limit, new_name)
            if new is not None:
                return self._add_function(proj, fn, new, syms, agent.id, new_name)
        head, tail = self._cut(proj, fn, addr, new_name, syms, agent.id, "split", f"{fn['name']} / {new_name}")
        return (f"✅ split: {fn['name']} is now {console.function_insns(head)} instructions; {new_name} at "
                f"0x{addr:08X} has {console.function_insns(tail)}. Continue with {fn['name']} "
                "(get_function shows its new assembly).")

    def _add_function(self, proj, prev, new, syms: SymbolTable, agent_id: int | None, new_name: str) -> str:
        """Add a function the splitter missed after `prev`; it starts at its own address and mode."""
        console = self.console(proj)
        syms.add(Symbol(new_name, new.vram, new.size, "func"))
        target = self._make_target(proj, new, syms)
        meta = console.function_meta(new)
        with self.store.tx() as db:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                       "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (proj["id"], new_name, new.size, console.function_insns(new), target.code,
                        json.dumps(target.to_json()), console.function_asm(new), None, prev["unit"], new.vram, new.rom,
                        console.function_status(new), json.dumps(meta) if meta else None))
            db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (proj["id"], new_name, new.vram, new.size, "func"))
            self._event(db, proj["id"], agent_id, "split", f"{prev['name']} / {new_name}")
        self._xrefs.pop(proj["id"], None)
        self._ops.pop(proj["id"], None)
        return f"✅ new function {new_name} at 0x{new.vram:08X}, {new.size} bytes."

    def _cut(self, proj, fn, addr: int, new_name: str, syms: SymbolTable, agent_id: int | None, kind: str, detail: str,
             tail_meta: dict | None = None):
        """Cut a MIPS function in two at `addr`: the head keeps its row, the tail becomes `new_name` (status 'asm'
        with `tail_meta` when it isn't code agents should work on). Returns (head, tail)."""
        console = self.console(proj)
        try:
            head, tail = console.split_function(fn, addr, new_name)
        except ValueError as e:
            raise NetworkError(str(e)) from None
        syms.add(Symbol(new_name, addr, tail.size, "func"))
        old = syms.by_name.get(fn["name"])
        if old is not None:
            old.size = head.size
        th, tt = self._make_target(proj, head, syms), self._make_target(proj, tail, syms)
        with self.store.tx() as db:
            meta = console.function_meta(tail)
            if tail_meta:
                meta.update(tail_meta)
            db.execute("UPDATE functions SET size=?, n_insns=?, target=?, relocs=?, asm=?, seed=NULL, best_score=NULL "
                       "WHERE id=?", (head.size, console.function_insns(head), th.code, json.dumps(th.to_json()),
                                      console.function_asm(head), fn["id"]))
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                       "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (proj["id"], new_name, tail.size, console.function_insns(tail), tt.code, json.dumps(tt.to_json()),
                        console.function_asm(tail), None, fn["unit"], addr, tail.rom,
                        "asm" if tail_meta else console.function_status(tail), json.dumps(meta) if meta else None))
            db.execute("UPDATE symbols SET size=? WHERE project_id=? AND name=?", (head.size, proj["id"], fn["name"]))
            db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (proj["id"], new_name, addr, tail.size, "func"))
            self._event(db, proj["id"], agent_id, kind, detail)
        self._xrefs.pop(proj["id"], None)
        return head, tail

    def request_boundary(self, agent: Agent, slug: str, function: str, end: str, reason: str) -> str:
        """An agent's report that a function really ends earlier: what follows is alignment padding, linker leftovers
        or data, not code anyone wrote. Changing the function list is the user's call, so it waits for them on the
        project page (resolve_boundary); approved, the function is cut there and the rest set aside. A project
        decompdecomp made (PC) cuts it once agents agree instead (ForkEngine.report_boundary)."""
        if (fork := self.fork_slug(slug)):
            return self.forks.report_boundary(agent, fork, function, end, reason)
        self._rom_only(slug, "report_boundary")
        if self._link(slug) is not None:
            raise NetworkError("in a team room the host's project decides where functions end: tell the host (add_note)")
        fn = self._function(function, slug)
        proj = self._project(slug)
        console = self.console(proj)
        if arch.BOUNDARIES not in console.features:
            raise NetworkError(f"boundary reports aren't supported for {console.name} yet (ROADMAP.md)")
        if fn["status"] == "matched":
            raise NetworkError(f"{fn['name']} is matched: its boundaries are proven")
        m = _ADDRESS.match(str(end).strip())
        if not m:
            raise NetworkError("`end` must be an address like 0x80001234: where the function's last instruction ends")
        addr = int(m.group(1), 16)
        size = console.instruction_size(fn)
        off = addr - fn["vram"]
        if off <= 0 or off >= fn["size"] or off % size:
            word = "word" if size == 4 else "instruction"
            raise NetworkError(f"0x{addr:08X} isn't a {word} inside {fn['name']} "
                               f"(0x{fn['vram']:08X}, {fn['size']} bytes)")
        reason = " ".join(reason.split())[:600]
        if not reason:
            raise NetworkError("say what the bytes after it are (padding, linker leftovers, data) and how you can tell")
        order = "big" if proj["big_endian"] else "little"
        rest = " ".join(f"{int.from_bytes(fn['target'][at:at + size], order):0{2 * size}X}"
                        for at in range(off, min(fn["size"], off + 32), size))

        def change(cfg):
            items = cfg.setdefault("boundaries", [])
            items[:] = [r for r in items if not (r["status"] == "open" and r["function"] == fn["name"])]
            items.append({"id": max((r["id"] for r in items), default=0) + 1, "function": fn["name"], "vram": fn["vram"],
                          "end": addr, "cut": fn["size"] - off, "rest": rest, "reason": reason, "agent": agent.name,
                          "t": time.time(), "status": "open"})
        self._update_cfg(slug, change)
        return (f"Reported: {fn['name']} ends at 0x{addr:08X} ({fn['size'] - off} bytes after it set aside if the user "
                f"approves). Until then it stays as it is; release it or work on with its current size.")

    def join_functions(self, agent: Agent, project: str | None, function: str, with_function: str, reason: str) -> str:
        """An agent says two adjacent symbols are one function the analysis cut: a piece with no prologue that uses
        registers set before it, an epilogue alone, a switch whose cases are in the next symbol. Once NOT_C_AGENTS
        agents of NOT_C_MODELS models say it of the same pair, the later symbol is joined into the earlier and it is
        one function again (the tails' attempts and notes kept, reopened to match). A fork rebuilds to check a join
        (ForkEngine.propose_join); a ROM has no rebuild, so agreement stands behind it and matching the joined
        function proves it. Undo a wrong join by splitting it again."""
        if (fork := self.fork_slug(project)):
            return self.forks.propose_join(agent, fork, function, with_function, reason)
        self._rom_only(project, "join_functions")
        if self._link(project) is not None:
            raise NetworkError("in a team room the host's project decides where functions begin and end: "
                               "tell the host (add_note)")
        reason = " ".join(reason.split())[:600]
        if not reason:
            raise NetworkError("say what shows they're one function (no prologue and it uses registers set before "
                               "it, an epilogue alone, a switch whose cases are in the next symbol)")
        a, b = self._function(function, project), self._function(with_function, project)
        if a["id"] == b["id"]:
            raise NetworkError("name two different functions")
        proj = self._project(project)
        console = self.console(proj)
        if arch.BOUNDARIES not in console.features:
            raise NetworkError(f"joining functions isn't supported for {console.name} yet (ROADMAP.md)")
        for f in (a, b):
            if f["status"] == "matched" or f["matched_source"]:
                raise NetworkError(f"{f['name']} is matched: a join would change it, so it can't be one")
        first, last = sorted((a, b), key=lambda f: (f["vram"] if f["vram"] is not None else 0, f["id"]))
        if first["vram"] is None or not first["size"] or last["vram"] < first["vram"] + first["size"]:
            nxt = (first["vram"] or 0) + (first["size"] or 0)
            raise NetworkError(f"{last['name']} isn't after {first['name']} (which ends at 0x{nxt:08X}): "
                               "name them in order, the earlier one first")
        # A join is within one function, so the two pieces must be one contiguous run. rom_offset identifies which
        # overlay *instance* a piece belongs to; vram+unit does not, because overlays alias the same vram — and even
        # the same source unit — across different ROM copies. So when rom_offset is known, require the pieces to
        # advance in rom_offset exactly as in vram (same instance, truly adjacent) and select by rom_offset range;
        # that excludes an aliased sibling at the same address whether it differs in unit or shares it.
        rom_known = first["rom_offset"] is not None and last["rom_offset"] is not None
        rom_contiguous = rom_known and last["rom_offset"] - first["rom_offset"] == last["vram"] - first["vram"]
        if rom_known:
            if not rom_contiguous:
                raise NetworkError(f"{first['name']} and {last['name']} aren't one contiguous run in the ROM "
                                   "(different overlays or ROM copies of the same address); a join is within one function")
            between = self.store.one("SELECT 1 FROM functions WHERE project_id=? AND rom_offset>? AND rom_offset<?",
                                     proj["id"], first["rom_offset"], last["rom_offset"])
        else:
            if first["unit"] != last["unit"]:
                raise NetworkError(f"{first['name']} and {last['name']} are in different units; a join is within one")
            between = self.store.one("SELECT 1 FROM functions WHERE project_id=? AND unit=? AND vram>? AND vram<?",
                                     proj["id"], first["unit"], first["vram"], last["vram"])
        if between:
            raise NetworkError(f"{last['name']} isn't the next function after {first['name']}: a join is to the "
                               "very next symbol; join the nearer pair first for a longer run")
        key = f"{first['name']}+{last['name']}"
        refused = (self.cfg(proj).get("joins_refused") or {}).get(key)
        if refused:
            return (f"Joining {first['name']} and {last['name']} was tried and refused: {refused}. "
                    "Work on them as they are.")
        with self.store.tx() as db:
            meta = json.loads(db.execute("SELECT meta FROM functions WHERE id=?", (first["id"],)).fetchone()["meta"] or "{}")
            said = meta.setdefault("joins", {}).setdefault(key, {})
            said[agent.name] = {"model": model_of(agent.name), "why": reason[:2000], "t": time.time()}
            db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), first["id"]))
            self._event(db, proj["id"], agent.id, "merge", f"{first['name']} + {last['name']}: {reason[:200]}")
        models = {v["model"] for v in said.values()}
        if len(said) < NOT_C_AGENTS or len(models) < NOT_C_MODELS:
            return (f"Your report is counted ({len(said)} of {NOT_C_AGENTS} agents, {len(models)} of {NOT_C_MODELS} "
                    f"models): {first['name']} and {last['name']} are joined once enough agree. Meanwhile, claim "
                    "another function.")
        return self._apply_join(proj, first, last, agent, sorted(said))

    def _apply_join(self, proj, first, last, agent: Agent, said: list[str]) -> str:
        console = self.console(proj)
        pid = proj["id"]
        start, end = first["vram"], last["vram"] + last["size"]
        if first["rom_offset"] is not None and last["rom_offset"] is not None:
            # select this overlay instance's own ROM run; aliased copies share the vram window but not the ROM range
            rows = self.store.all("SELECT * FROM functions WHERE project_id=? AND rom_offset>=? AND rom_offset<? "
                                  "ORDER BY rom_offset, id", pid, first["rom_offset"],
                                  last["rom_offset"] + last["size"])
        else:
            rows = self.store.all("SELECT * FROM functions WHERE project_id=? AND vram>=? AND vram<? AND unit=? "
                                  "ORDER BY vram, id", pid, start, end, first["unit"])
        if len(rows) < 2 or rows[0]["id"] != first["id"]:
            raise NetworkError("the functions between them changed; look again and report the pair afresh")
        tails = rows[1:]
        key = f"{first['name']}+{last['name']}"
        try:
            joined = console.join_saved_functions(rows, start, end, lambda address, n: self.reader(proj)(address, n))
        except ValueError:
            why = "the bytes between them aren't one run of code (a real gap sits between them)"
            self._update_cfg(proj["slug"], lambda c: c.setdefault("joins_refused", {}).update({key: why}))
            return f"{len(said)} agents agreed, but {first['name']} and {last['name']} couldn't be joined: {why}."
        target = self._make_target(proj, joined, self.symbols(pid))
        with self.store.tx() as db:
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,seed=NULL,best_score=NULL,"
                       "best_source=NULL,status='open',attempts=attempts+? WHERE id=?",
                       (joined.size, console.function_insns(joined), target.code, json.dumps(target.to_json()),
                        console.function_asm(joined), sum(t["attempts"] or 0 for t in tails), first["id"]))
            for tail in tails:
                db.execute("UPDATE attempts SET function_id=? WHERE function_id=?", (first["id"], tail["id"]))
                db.execute("UPDATE OR IGNORE twin_misses SET function_id=? WHERE function_id=?", (first["id"], tail["id"]))
                db.execute("DELETE FROM twin_misses WHERE function_id=?", (tail["id"],))
                db.execute("DELETE FROM claims WHERE function_id=?", (tail["id"],))
                db.execute("DELETE FROM waits WHERE function_id=? OR needs_id=?", (tail["id"], tail["id"]))
                db.execute("UPDATE notes SET function_id=? WHERE function_id=?", (first["id"], tail["id"]))
                db.execute("DELETE FROM functions WHERE id=?", (tail["id"],))
                db.execute("DELETE FROM symbols WHERE project_id=? AND address=? AND kind='func'", (pid, tail["vram"]))
            db.execute("UPDATE symbols SET size=? WHERE project_id=? AND name=? AND kind='func'",
                       (joined.size, pid, first["name"]))
            self._event(db, pid, agent.id, "merge", f"{first['name']} + {', '.join(t['name'] for t in tails)}: joined")
        self._syms.pop(pid, None)
        if hasattr(self, "_xrefs"):
            self._xrefs.pop(pid, None)
        if hasattr(self, "_ops"):
            self._ops.pop(pid, None)
        tail_names = ", ".join(t["name"] for t in tails)
        return (f"✅ joined: {tail_names} {'is' if len(tails) == 1 else 'are'} part of {first['name']} now "
                f"({console.function_insns(joined)} instructions, {joined.size} bytes). Continue with "
                f"{first['name']} (get_function shows its assembly).")

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
        if (fork := self.fork_slug(project)):  # the project's own config and files (ForkEngine.rename_symbol)
            return self.forks.rename_symbol(agent, fork, old, new, message)
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
        trial = syms.copy()  # with every overlay twin's name (#117 review)
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
            at = self.store.all("SELECT id, name, size FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
            renamed = [r for r in at if r["name"] == old] or (at if len(at) == 1 else [])
            if any(r["name"] == new for r in at) and not any(r["name"] == new for r in renamed):
                # the other overlay function at this address has it: the names would collide (#117 review)
                raise NetworkError(f"`{new}` already names another function at 0x{addr:08X} (an overlay's)")
            addresses[old] = addr
            if old in trial.by_name:
                trial.rename(old, new)
            else:
                fnrow = at[0] if at else None
                current, _ = syms.name_at(addr)
                base = syms.by_name.get(current) if current != f"D_{addr:08X}" else None
                sym = Symbol(new, addr, fnrow["size"] if fnrow else (base.size if base and base.address == addr else 0),
                             "func" if fnrow else (base.kind if base else "data"))
                if len(at) > 1:  # an overlay twin still called by its address's name: the other twin's name stays
                    trial.alias(sym)
                else:
                    trial.add(sym)
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
                # By name: an address overlays share names one of their functions, never all of them (#107 review).
                # A name only the symbols had (a function whose row kept its address's name) goes by the address.
                if not db.execute("UPDATE functions SET name=? WHERE project_id=? AND name=?",
                                  (new, proj["id"], old)).rowcount:
                    db.execute("UPDATE functions SET name=? WHERE project_id=? AND vram=? AND (SELECT COUNT(*) FROM "
                               "functions WHERE project_id=? AND vram=?)=1", (new, proj["id"], addr, proj["id"], addr))
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

    def set_claim_order(self, slug: str, order: str) -> str:
        """Which functions helpers claim without a name take first, whatever strategy they ask for; "" is the
        agent's own choice (easiest, or finish in project mode)."""
        order = (order or "").strip()
        if order not in ("", *CLAIM_ORDERS):
            raise NetworkError(f"claim order must be left empty (the default) or be one of {', '.join(CLAIM_ORDERS)}")
        self._update_cfg(slug, lambda cfg: cfg.__setitem__("claim_order", order) if order
                         else cfg.pop("claim_order", None))
        return order

    def claim_order(self, slug: str) -> str:
        order = self.cfg(self._project(slug)).get("claim_order") or ""
        return order if order in CLAIM_ORDERS else ""

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
