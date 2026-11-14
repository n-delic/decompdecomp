"""Changing the project: names (rename_symbol), the shared header, notes, splitting a function, function-end
reports, and what the user steers (instructions, title, priorities).
"""

from __future__ import annotations

import json
import re
import threading
import time

from ... import arch
from ...network import CLAIM_ORDERS, Agent, NetworkError
from ...rom import split
from ...rom.symbols import IDENT, Symbol, SymbolTable
from .common import _ADDRESS


class EditMixin:
    def split_function(self, agent: Agent, project: str | None, function: str, at: str, new_name: str = "", *,
                       by_you: bool = False) -> str:
        self._rom_only(project, "split_function")
        link = self._link(project)
        if link is not None:
            name = self._function(function, project)["name"]
            text = link.call("/collab/split", agent, function=name, at=at, new_name=new_name)
            if text.startswith("✅"):
                link.sync()
            return text
        fn = self._function(function, project)
        proj = self.project_store(fn['project_id']).one("SELECT * FROM projects WHERE id=?", fn["project_id"])
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
            following = [r["vram"] for r in self.project_store(fn['project_id']).all(
                "SELECT vram FROM functions WHERE project_id=? AND vram>?", proj["id"], addr)]
            limit = min(following) if following else fn["vram"] - (fn["rom_offset"] or 0) + len(rom)
            new = console.function_at(rom, addr, limit, new_name)
            if new is not None:
                return self._add_function(proj, fn, new, syms, agent.id, new_name)
        if inside and not by_you and (why := self._split_runs_on(proj, fn, addr)):
            raise NetworkError(why)
        head, tail = self._cut(proj, fn, addr, new_name, syms, agent.id, "split", f"{fn['name']} / {new_name}")
        return (f"✅ split: {fn['name']} is now {console.function_insns(head)} instructions; {new_name} at "
                f"0x{addr:08X} has {console.function_insns(tail)}. Continue with {fn['name']} "
                "(get_function shows its new assembly).")

    def _split_runs_on(self, proj, fn, addr: int) -> str | None:
        """Why an agent's cut at `addr` would leave the first part running on into the second, or None.

        Agents cut 16-byte getters before their `jr $ra` and took a function's delay slot (an N64 game had six such
        entries); each then needed a person to revoke a match of the empty tail and join it back. A cut is refused
        when the first part's last instruction needs the next word as its delay slot, or when the first part falls
        through its end without a transfer just before it (after a call that never returns, the next function may
        really start there). A person can still split anything (split_rom_by_you)."""
        facts = arch.get(proj["arch"]).boundary_instruction
        console = self.console(proj)
        width = console.instruction_size(fn)
        code = (fn["target"] or b"")[:addr - fn["vram"]]
        if facts is None or width != 4 or len(code) < 4:
            return None
        order = "big" if proj["big_endian"] else "little"
        last = facts(int.from_bytes(code[-4:], order), addr - 4)
        where = f"0x{addr:08X}"
        if last["delay_slot"]:
            return (f"{where} is the delay slot of `{last['instruction']}`, which belongs to {fn['name']}: split after "
                    "it. If the code really starts there, report it with report_boundary so a person can decide.")
        before = facts(int.from_bytes(code[-8:-4], order), addr - 8) if len(code) >= 8 else None
        if console.boundary_reason(code) and not (before and (before["terminal"] or before["call"])):
            return (f"Cut at {where}, {fn['name']} would run on into the new function: nothing before {where} returns "
                    "or jumps away (a call that never returns is fine). Split after the function's return and its delay slot, or report the entry with "
                    "report_boundary so a person can decide.")
        return None

    def _add_function(self, proj, prev, new, syms: SymbolTable, agent_id: int | None, new_name: str) -> str:
        """Add a function the splitter missed after `prev`; it starts at its own address and mode."""
        console = self.console(proj)
        syms.add(Symbol(new_name, new.vram, new.size, "func"))
        target = self._make_target(proj, new, syms)
        meta = console.function_meta(new)
        with self.project_store(proj['id']).tx() as db:
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

    def split_rom_by_you(self, slug: str, function: str, at: str, new_name: str = "") -> str:
        """Operator correction of a merged ROM entry; keep both sides as code requiring a fresh judgement."""
        self._rom_only(slug, "split_rom_by_you")
        if self._link(slug) is not None or slug in self.rooms:
            raise NetworkError("leave the shared room before changing ROM function boundaries")
        proj, fn = self._project(slug), self._function(function, slug)
        if self.project_store(proj['id']).one("SELECT 1 FROM claims WHERE function_id=? AND expires_at>?", fn["id"], time.time()):
            raise NetworkError("release the function's active claim before splitting it")
        image, _ = self._rom(proj)
        if image[fn["rom_offset"]:fn["rom_offset"] + fn["size"]] != fn["target"]:
            raise NetworkError("the function differs from the original image")
        agent, _ = self.local_agent("you")
        return self.split_function(agent, slug, function, at, new_name, by_you=True)

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
        with self.project_store(proj['id']).tx() as db:
            db.execute("UPDATE functions SET size=?, n_insns=?, target=?, relocs=?, asm=?, seed=NULL, best_score=NULL, best_source=NULL "
                       "WHERE id=?", (head.size, len(head.lines), th.code, json.dumps(th.to_json()),
                                      self._asm_source(head), fn["id"]))
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset,"
                       "status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (proj["id"], new_name, tail.size, console.function_insns(tail), tt.code, json.dumps(tt.to_json()),
                        console.function_asm(tail), None, fn["unit"], addr, tail.rom,
                        "asm" if tail_meta else console.function_status(tail), json.dumps(tail_meta) if tail_meta else None))
            db.execute("UPDATE symbols SET size=? WHERE project_id=? AND name=?", (head.size, proj["id"], fn["name"]))
            db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (proj["id"], new_name, addr, tail.size, "func"))
            self._event(db, proj["id"], agent_id, kind, detail)
        self._xrefs.pop(proj["id"], None)
        return head, tail

    def _bare_return(self, proj, fn) -> bool:
        """Whether matched `fn` is only a return and its delay slot that nothing calls or points at: the end of the
        function before it, cut off and matched as an empty function. An N64 game had two getters cut before their
        `jr $ra` this way; a person's join may take such a piece back, giving up a match that was never a function
        (its attempts, with that C, move to the joined entry like every piece's)."""
        facts = arch.get(proj["arch"]).boundary_instruction
        code = fn["target"] or b""
        if facts is None or len(code) != 8 or code[4:] != bytes(4):
            return False
        order = "big" if proj["big_endian"] else "little"
        if not facts(int.from_bytes(code[:4], order), fn["vram"])["return"]:
            return False
        if self.xrefs(proj)["called_by"].get(fn["vram"]):
            return False
        image, _ = self._rom(proj)
        word = fn["vram"].to_bytes(4, order)
        return not any(at % 4 == 0 for at in _find_all(image, word))

    def join_rom_functions(self, slug: str, names: list[str], reason: str) -> str:
        """An explicit operator correction: join consecutive, unmatched pieces of one ROM segment.

        Keep internal symbols at their original addresses, so callers and already matched C stay valid.
        Matching still requires a fresh compile and the normal linked code/data comparison.
        """
        self._rom_only(slug, "join_rom_functions")
        if self._link(slug) is not None or slug in self.rooms:
            raise NetworkError("leave the shared room before changing ROM function boundaries")
        if len(names) < 2 or len(names) != len(set(names)):
            raise NetworkError("choose at least two distinct consecutive pieces")
        proj = self._project(slug)
        pieces = sorted((self._function(name, slug) for name in names), key=lambda f: f["rom_offset"])
        head = pieces[0]
        if head["status"] == "matched" or any(f["status"] == "matched" and not self._bare_return(proj, f)
                                              for f in pieces[1:]):
            raise NetworkError("a matched function cannot be joined")
        if any(f["unit"] != head["unit"] for f in pieces):
            raise NetworkError("the pieces must belong to the same source unit")
        for a, b in zip(pieces, pieces[1:]):
            gap = b["rom_offset"] - (a["rom_offset"] + a["size"])
            if gap not in (0, -4) or b["vram"] - a["vram"] != b["rom_offset"] - a["rom_offset"]:
                raise NetworkError("the pieces must be consecutive in the original image and memory")
            if gap == -4 and a["target"][-4:] != b["target"][:4]:
                raise NetworkError("overlapping delay-slot bytes disagree")
        ids = [f["id"] for f in pieces]
        if any(self.project_store(proj['id']).one("SELECT 1 FROM claims WHERE function_id=?", fid) for fid in ids):
            raise NetworkError("release the pieces' claims before joining them")
        image, segments = self._rom(proj)
        end = pieces[-1]["rom_offset"] + pieces[-1]["size"]
        raw = image[head["rom_offset"]:end]
        if any(image[f["rom_offset"]:f["rom_offset"] + f["size"]] != f["target"] for f in pieces):
            raise NetworkError("a piece differs from the original image")
        owner = next((seg for seg in segments if seg.rom_start <= head["rom_offset"] < seg.rom_end), None)
        end = head["rom_offset"] + len(raw)
        if owner is None or end > owner.rom_end or image[head["rom_offset"]:end] != raw:
            raise NetworkError("the pieces do not describe one unchanged original segment")
        relocations = set()
        for f in pieces:
            offset = f["rom_offset"] - head["rom_offset"]
            relocations.update((r[0] + offset, *r[1:]) for r in json.loads(f["relocs"]))
        relocs = sorted(relocations)
        meta = {k: v for k, v in json.loads(head["meta"] or "{}").items()
                if k not in ("asm_code", "asm_twin", "not_c", "set_aside")}
        meta["joined_from"] = [{k: f[k] for k in ("name", "size", "vram", "rom_offset", "meta", "status")} for f in pieces]
        meta["reopened"] = True
        lines, labels = {}, {}
        for f in pieces:
            pending = []
            for text in f["asm"].splitlines():
                insn = split._INSN.match(text)
                if insn:
                    at = int(insn.group(1), 16)
                    lines[at] = text
                    labels.setdefault(at, set()).update(pending)
                    pending = []
                elif text.lstrip().startswith((".L", "glabel ", "jlabel ")):
                    pending.append(text)
        assembly = "\n".join("\n".join(sorted(labels.get(at, set()))) + "\n" + text
                             for at, text in sorted(lines.items()))
        assembly += f"\nendlabel {head['name']}\n"
        with self.project_store(proj['id']).tx() as db:
            for child in pieces[1:]:
                db.execute("UPDATE notes SET function_id=? WHERE function_id=?", (head["id"], child["id"]))
                db.execute("UPDATE attempts SET function_id=? WHERE function_id=?", (head["id"], child["id"]))
                db.execute("DELETE FROM functions WHERE id=?", (child["id"],))
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,status='open',meta=?,"
                       "best_source=NULL,best_score=NULL,seed=NULL,seed_key=NULL WHERE id=?",
                       (len(raw), len(raw) // 4, raw, json.dumps(relocs), assembly,
                        json.dumps(meta), head["id"]))
            db.execute("UPDATE symbols SET size=? WHERE project_id=? AND name=?",
                       (len(raw), proj["id"], head["name"]))
            self._event(db, proj["id"], None, "boundary", f"joined {', '.join(names)}: {reason[:600]}")
        self._syms.pop(proj["id"], None)
        self._xrefs.pop(proj["id"], None)
        self._ops.pop(proj["id"], None)
        self._warmed.pop(proj["id"], None)
        self.twins.forget(proj)
        self.bump_generation(proj["id"])
        return f"Joined {len(pieces)} original pieces as {head['name']} ({len(raw)} bytes); not matched until judged"

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

    def rename_symbol(self, agent: Agent, project: str | None, old: str, new: str, message: str = "", *, progress=None) -> str:
        from ..function_names import check_rename
        check_rename(old.strip(), new.strip())
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
        return self.rename_symbols(agent, project, [(old, new)], message, progress=progress)

    def rename_symbols(self, agent: Agent, project: str | None, pairs: list[tuple[str, str]], message: str = "", *, progress=None) -> str:
        """Rename several symbols at once (old -> new): the shared headers, matched sources and best attempts
        change together, after one check that every matched function still matches. One rename is the agents'
        rename_symbol; many at once are the PS2 kernel stubs named at import (consoles/ps2/syscalls.py).
        Renames in one project take turns (Engine._rename_locks)."""
        proj = self._project(project)
        lock = self._rename_locks.setdefault(proj["id"], threading.RLock())
        with lock:
            return self._rename_symbols(agent, proj, pairs, message, progress=progress)

    def _rename_symbols(self, agent: Agent, proj, pairs: list[tuple[str, str]], message: str = "", *, progress=None) -> str:
        pairs = [(old.strip(), new.strip()) for old, new in pairs]
        if not pairs:
            return "nothing to rename"
        from ..function_names import check_rename
        for old, new in pairs:
            check_rename(old, new)
        syms = self.symbols(proj["id"])
        trial = syms.copy()  # with every overlay twin's name (#117 review)
        addresses = {}
        folded = {}  # (new name in lower case) -> its address, for names in this batch
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
            # A function's saved C is src/<name>.c. macOS and Windows disks ignore capitals in file names, so two
            # functions named selectStateReset and SelectStateReset shared one file and an N64 game's saves stopped
            # (every sync found the other function's file where it meant to write).
            clash = self.project_store(proj["id"]).one(
                "SELECT name FROM functions WHERE project_id=? AND lower(name)=lower(?) AND vram<>? AND name<>? LIMIT 1",
                proj["id"], new, addr, old)
            if clash is None and folded.get(new.lower(), addr) != addr:
                clash = {"name": next(n for o, n in pairs if n.lower() == new.lower() and addresses.get(o) != addr)}
            if clash is not None:
                raise NetworkError(f"`{new}` differs from `{clash['name']}` only in capitals: their saved files would "
                                   "be one file on macOS and Windows. Choose a name that differs in more than capitals.")
            folded[new.lower()] = addr
            at = self.project_store(proj["id"]).all("SELECT id, name, size FROM functions WHERE project_id=? AND vram=?", proj["id"], addr)
            # An address name (func_8008949C) still resolves once its address has a real name. Two helpers renaming
            # one function at once both got through: the second added a second name at the address and renamed the
            # function, leaving its matched C defining the first name, so it no longer matched.
            # An address inside a larger data block (a 70 KB bss struct) has no name of its own yet: naming it adds
            # one, as a save restores them; only an address that already has a name is refused.
            current, offset = syms.name_at(addr)
            if (old not in syms.by_name and current != old and current in syms.by_name and offset == 0
                    and not any(r["name"] == old for r in at)):  # (an overlay twin may still go by the address name)
                raise NetworkError(f"`{old}` is called `{current}` now: rename `{current}` if the name should change")
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
        current_ctx = self._contexts(proj["id"], {""} | units)
        changed = {u: swap(c["header"]) for u, c in current_ctx.items() if word.search(c["header"])}
        from ..rename_scope import inventory
        indexed=inventory(self,proj,progress)
        all_words=set(n for pair in pairs for n in pair)
        # Read full rows only for actual definition/call/header/capture dependencies.
        ids=[r['id'] for r,words in indexed if r['vram'] in addresses.values() or words & all_words
             or any(re.search(r'\b'+re.escape(n)+r'\b',current_ctx[self._effective_unit(r,units)]['header']) for n in all_words)]
        matched=[r for r,_ in indexed]
        from ...function_rows import selected
        affected = selected(self.project_store(proj['id']), proj['id'], ids)
        rewritten={r['id']:swap(r['matched_source']) for r in affected if word.search(r['matched_source'] or '')}
        renamed_rows = [dict(r) | {"name": new_of.get(r["name"], r["name"])} for r in affected]
        if progress:
            progress({"done": 0, "total": len(affected), "phase": "Checking affected matches",
                      "message": f"Checking {len(affected)} affected matches; {len(matched)-len(affected)} unchanged matches need no rebuild."})
        header_for = lambda fn: changed.get(self._effective_unit(fn, units),
                                            current_ctx[self._effective_unit(fn, units)]["header"])
        builtin_guards={}
        protected_proj=proj
        self._sym_override[proj["id"]] = trial
        try:
            broken = self.regression_test(proj, None, sources=rewritten, only=renamed_rows, header_for=header_for,
                                          **({"progress": progress} if progress else {}))
            if broken:
                from ..rename_builtins import protect
                protected_proj,broken,builtin_guards=protect(self,proj,pairs,renamed_rows,rewritten,header_for,broken,progress)
        finally:
            self._sym_override.pop(proj["id"], None)
        if broken:
            listing = "\n".join(f"- {n}: {why}" for n, why in broken[:10])
            return f"❌ REJECTED: renaming breaks {len(broken)} matched function(s).\n{listing}"
        msg = message.strip() or ", ".join(f"rename {old} -> {new}" for old, new in pairs[:3]) + (" ..." if len(pairs) > 3 else "")
        with self.project_store(proj['id']).tx() as db:
            for old, new in pairs:
                addr = addresses[old]
                if db.execute("UPDATE symbols SET name=? WHERE project_id=? AND name=?", (new, proj["id"], old)).rowcount == 0:
                    s = trial.by_name[new]
                    db.execute("INSERT OR REPLACE INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                               (proj["id"], new, s.address, s.size, s.kind))
                renamed_rows = db.execute("SELECT id FROM functions WHERE project_id=? AND name=?", (proj["id"], old)).fetchall()
                if renamed_rows:
                    db.execute("UPDATE functions SET name=? WHERE project_id=? AND name=?", (new, proj["id"], old))
                else:
                    renamed_rows = db.execute("SELECT id FROM functions WHERE project_id=? AND vram=?", (proj["id"], addr)).fetchall()
                    if len(renamed_rows) == 1:
                        db.execute("UPDATE functions SET name=? WHERE id=?", (new, renamed_rows[0]["id"]))
                    else:
                        renamed_rows = []
                from ..function_names import record
                for renamed in renamed_rows:
                    record(db, renamed["id"], old, new)

            cfg=json.loads(db.execute('SELECT config FROM projects WHERE id=?',(proj['id'],)).fetchone()['config'])
            if builtin_guards:
                guarded=json.loads(protected_proj['config'])['function_compilers']
                for profile in builtin_guards.values():
                    key=next(k for k,v in guarded.items() if v==profile)
                    cfg.setdefault('function_compilers',{})[key]=profile
            if cfg.get('function_compilers'):
                for profile in cfg['function_compilers'].values():
                    profile['function']=new_of.get(profile.get('function'),profile.get('function'))
                db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))

            for u, header in changed.items():
                version = self._next_version(db, proj["id"])
                db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                           "VALUES(?,?,?,?,?,?,?)", (proj["id"], version, header, agent.id, msg, time.time(), u))
            for fid, src in rewritten.items():
                db.execute("UPDATE functions SET matched_source=?, best_source=? WHERE id=?", (src, src, fid))
            for r in db.execute("SELECT id, best_source FROM functions WHERE project_id=? AND status='open' "
                                "AND best_source IS NOT NULL", (proj["id"],)).fetchall():
                if word.search(r["best_source"]):
                    db.execute("UPDATE functions SET best_source=? WHERE id=?", (swap(r["best_source"]), r["id"]))
            self._event(db, proj["id"], agent.id, "rename", msg if len(pairs) > 1 else f"{pairs[0][0]} → {pairs[0][1]}")
        self._syms.pop(proj["id"], None)
        guard_note=(f" Builtin optimizations disabled in {len(builtin_guards)} affected function profile(s), verified byte-exact." if builtin_guards else "")
        if len(pairs) == 1:
            (old, new), = pairs
            return (f"✅ 0x{addresses[old]:08X} is now `{new}` (was `{old}`); {len(affected)} affected matches checked; {len(rewritten)} matched sources updated, "
                    f"all still match."+guard_note)
        return f"✅ renamed {len(pairs)} symbols; {len(affected)} affected matches checked; {len(rewritten)} matched sources updated, all still match."+guard_note

    def edit_context(self, agent: Agent, project: str | None, old: str, new: str, message: str,
                     function: str | None = None, *, progress=None) -> str:
        self._rom_only(project, "edit_context")
        link = self._link(project)
        if link is None:
            return super().edit_context(agent, project, old, new, message, function, progress=progress)
        text = link.call("/collab/context", agent, old=old, new=new, message=message, function=function)
        if text.startswith("✅"):
            link.sync()
        return text

    def add_note(self, agent: Agent, project: str | None, text: str, function: str | None = None, *, score_before=None, score_after=None) -> str:
        link = self._link(project)
        if link is None:
            return super().add_note(agent, project, text, function, score_before=score_before, score_after=score_after)
        name = self._function(function, project)["name"] if function else None
        return link.call("/collab/note", agent, text=text, function=name, score_before=score_before, score_after=score_after) or "Noted, and shared with the room."

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
        with self.project_store(proj['id']).tx() as db:
            db.execute("UPDATE projects SET name=? WHERE id=?", (title, proj["id"]))
        return title

    def set_priority(self, slug: str, name: str, on: bool = True) -> list[str]:
        """Functions the user wants done next: automatic claims take these first."""
        # Claim order is metadata, not a compilation. Let autosave publish it;
        # a saved-code recheck must not hold the priority button for minutes.
        with self.progress.view(), self.progress._project_lock(slug):
            fn = self._function(name, slug)

            def change(cfg):
                items = [n for n in cfg.get("priority", []) if n != fn["name"]]
                cfg["priority"] = ([fn["name"]] + items) if on else items
            cfg = self._update_cfg(slug, change, synchronize=False)
            self.progress.foreground_changed(slug)
            return cfg["priority"]


def _find_all(data: bytes, needle: bytes):
    """Every offset of `needle` in `data`."""
    at = data.find(needle)
    while at >= 0:
        yield at
        at = data.find(needle, at + 1)
