"""Struct layouts guessed from a game's code (structs.py), for agents: the guess_struct tool, the brief's lines about
a function's pointer arguments (briefs.py calls `_struct_brief`), and declarations a draft's context can use. A
project's (PC: Console.structs) are answered by forkengine with the same text (guess_text, brief_lines).

Where the game's own debug information gives a function's prototype (consoles/ps2/debuginfo.py), its real types are shown instead of a
guess, and where matched code already gave the type a name, agents are told to use that one.
"""

from __future__ import annotations

import functools
import json
import logging
import re

from ... import arch, structs
from ...network import NetworkError
from .common import _ADDRESS

ARGUMENT_TEXT = re.compile(r"^(?:arg|a|\$a)?([0-3])$|^(this|ecx)$", re.I)
RETURN_TEXT = ("return", "ret", "v0", "result")
PATH_STEP = re.compile(r"\+\s*(0x[0-9A-Fa-f]+|\d+)")
BRIEF_ARGUMENTS = (structs.THIS, 0, 1, 2, 3)  # x86's `this` (ecx), then a0-a3 (MIPS) or the first four on the stack
SHOWN_MATCHED = 3  # matched functions named as already using a type


def parse_argument(argument: str) -> tuple[str, list[int], int | str | None]:
    """What an agent asks about: (its text before any "+", the pointer fields to follow after it, and what it is:
    an argument's number (structs.THIS for `this`), structs.RETURN, or None for a global)."""
    base, *_ = argument.split("+", 1)
    base = base.strip()
    path = [int(step, 0) for step in PATH_STEP.findall(argument[len(base):])]
    if (m := ARGUMENT_TEXT.match(base or "arg0")):
        return base, path, structs.THIS if m.group(2) else int(m.group(1))
    return base, path, structs.RETURN if base.lower() in RETURN_TEXT else None


def guess_text(found: structs.Structs, key: tuple, path: list[int], what: str, name: str, argument: str,
               label, keep: str) -> list[str]:
    """guess_struct's answer, but for the types matched code gives it: the layout as C, where it disagrees, its
    pointer fields and where else the type flows. `keep`: how agents share what they confirm."""
    t = found.type_of(key, path)
    lay = found.layout(t) if t is not None else None
    where = what + "".join(f"+0x{step:X}" for step in path)
    if not lay or not lay.fields:
        return [f"No loads or stores through {where} of {name} found, in it or in the functions it shares the pointer "
                f"with: it may not be a pointer, or it's only handed on to a function many others call (those keep "
                f"a type of their own)."]
    type_name = found.name(t, label)
    lines = [f"Likely layout of {where} of {name}: {type_name}, from the loads and stores of {len(lay.functions)} "
             f"function{'s' if len(lay.functions) != 1 else ''}. A guess from the code: confirm it with "
             f"compile_and_diff, then {keep}, under this name so other agents find it.", "```c",
             structs.c_text(lay, type_name, lambda c: found.name(c, label)), "```"]
    disagree = [f for f in lay.fields if f.others]
    if disagree:
        lines.append(f"{len(disagree)} field{'s are' if len(disagree) != 1 else ' is'} also read with another type "
                     f"(see the comments): a union, a copy, or two structs taken for one.")
    pointers = [f for f in lay.fields if f.points_to is not None]
    if pointers:
        lines.append("Its pointer fields point to likely structs too: " + ", ".join(
            f'guess_struct("{name}", "{argument.strip() or "arg0"}+0x{f.offset:X}")' for f in pointers[:6]))
    return lines + _struct_flows(found, lay, key, label)


def _struct_flows(found, lay, key, label) -> list[str]:
    """Where else the type flows: functions that take it or return it, globals that are one, pointer fields."""
    named = [m for m in lay.members if m[0] in structs.NAMED and m != key]
    said = []
    for kind, *rest in named[:structs.SHOWN_MEMBERS]:
        said.append(f"the global {label(rest[0])}" if kind == structs.GLOBAL else
                    f"{label(rest[0])} {structs.argument_name(rest[1])}" if kind == structs.ARGUMENT else
                    f"what {label(rest[0])} returns")
    said += [f"+0x{offset:X} of {found.name(parent, label)}" for parent, offset in lay.pointed_from[:6]]
    if not said:
        return []
    more = len(named) - structs.SHOWN_MEMBERS
    return ["The same type: " + ", ".join(said) + (f", and {more} more" if more > 0 else "")]


def brief_lines(found: structs.Structs, addr: int, name: str, label) -> list[str]:
    """The brief's lines: each pointer argument's likely layout in one line."""
    lines = []
    for n in BRIEF_ARGUMENTS:
        t = found.type_of((structs.ARGUMENT, addr, n))
        lay = found.layout(t) if t is not None else None
        if lay and lay.fields:
            lines.append(f"- {structs.argument_name(n)}: {found.name(t, label)}, {structs.summary(lay)}")
    if not lines:
        return []
    return ["", f"### Likely struct layouts (from loads and stores across the game; guess_struct("
                f"\"{name}\", \"arg0\") has one as C)"] + lines


def where_read() -> str:
    """Which processors guess_struct reads, said where it can't."""
    return f"guess_struct reads {', '.join(a.name for a in arch.ARCHES if arch.STRUCTS in a.features)} code for now"


class LayoutMixin:
    def structs(self, proj) -> structs.Structs | None:
        """The types found in a game's code (structs.py), kept with the cross-references like the classes (renames
        keep addresses; splits and cuts drop both). About 2 s on GTA San Andreas, once. None: no reader for its
        processor."""
        if not self.is_rom(proj) or arch.STRUCTS not in self.console(proj).features:
            return None
        x = self.xrefs(proj)
        if "structs" not in x:
            rows = self.store.all("SELECT vram, target, relocs FROM functions WHERE project_id=? AND target IS NOT NULL",
                                  proj["id"])
            syms = self.symbols(proj["id"])
            read = functools.partial(arch.get(self.console(proj).arch).field_accesses,
                                     big_endian=bool(self.cfg(proj).get("big_endian", True)))
            x["structs"] = structs.recover(((r["vram"], r["target"], json.loads(r["relocs"])) for r in rows), read,
                                           lambda a: a - syms.name_at(a)[1])
        return x["structs"]

    def _struct_key(self, fn, argument: str, syms) -> tuple[tuple, list[int], str]:
        """(structs key, pointer fields to follow, what to call it) for what an agent asks about: arg0..arg3, return,
        or a global; "+0x14" after it follows the pointer at that offset."""
        base, path, n = parse_argument(argument)
        if n == structs.RETURN:
            return (structs.RETURN, fn["vram"]), path, "what it returns"
        if n is not None:
            return (structs.ARGUMENT, fn["vram"], n), path, structs.argument_name(n)
        m = _ADDRESS.match(base)
        addr = int(m.group(1), 16) if m else syms.resolve(base)
        if addr is None:
            raise NetworkError(f"`{argument}`: say arg0 to arg3, return, or a global's name or address (and +0x14 to "
                               f"follow the pointer at 0x14)")
        start = addr - syms.name_at(addr)[1]
        return (structs.GLOBAL, start), path, f"the global {syms.label(start)}"

    def guess_struct(self, project: str | None, function: str, argument: str = "arg0") -> str:
        """The likely struct behind one of a function's pointers, as C: from every function's loads and stores that
        reach the same type, where else it flows, and the type matched code already uses for it."""
        if (fork := self.fork_slug(project)):
            return self.forks.guess_struct(fork, function, argument)
        proj = self._project(project)
        fn = self._function(function, proj["slug"])
        syms = self.symbols(proj["id"])
        key, path, what = self._struct_key(fn, argument, syms)
        known = self._debug_type(fn, key, path, what, proj)
        if known:
            return known
        found = self.structs(proj)
        if found is None:
            raise NetworkError(f"{where_read()}, not this game's")
        lines = guess_text(found, key, path, what, fn["name"], argument, syms.label,
                           "add what holds to context.h with edit_context")
        t = found.type_of(key, path)
        lay = found.layout(t) if t is not None else None
        if lay and lay.fields:
            lines += self._matched_types(proj, found, lay)
        return "\n".join(lines)

    def _matched_types(self, proj, found, lay) -> list[str]:
        """The names matched code already gives this type (an argument declared `CPed *`), with the header's
        definition: agents should use that, not a new struct."""
        takes = [(m[1], m[2]) for m in lay.members if m[0] == structs.ARGUMENT]
        vrams = sorted({f for f, _ in takes})
        if not vrams:
            return []
        rows = self.store.all(f"SELECT name, vram, matched_source FROM functions WHERE project_id=? AND status='matched' "
                              f"AND vram IN ({','.join('?' * len(vrams))})", proj["id"], *vrams)
        by_vram = {r["vram"]: r for r in rows}
        seen: dict[str, list[str]] = {}
        for f, n in takes:
            r = by_vram.get(f)
            ctype = structs.argument_type(r["matched_source"] or "", r["name"], n) if r else None
            if ctype and (name := structs.type_name(ctype)):
                seen.setdefault(name, []).append(f"{r['name']} arg{n}")
        if not seen:
            return []
        header = (self._context(proj["id"], "") or {"header": ""})["header"]
        lines = []
        for name, where in sorted(seen.items(), key=lambda item: -len(item[1])):
            said = f"Matched code already calls it {name} ({', '.join(where[:SHOWN_MATCHED])})"
            if (text := structs.definition(header, name)):
                lines += [f"{said}: use that type. Its definition in context.h:", "```c", text, "```"]
            else:
                lines.append(f"{said}, defined in its own source, not in context.h (get_function shows it): move it "
                             f"there with edit_context, so every agent uses one struct.")
        return lines

    def _debug_type(self, fn, key, path, what, proj) -> str | None:
        """What the game's own debug information says the argument is, when it says: the real type, so no
        guess is needed."""
        debug = json.loads(fn["meta"] or "{}").get("debug") if "meta" in fn.keys() else None
        if not debug or not debug.get("prototype") or key[0] != structs.ARGUMENT or path:
            return None
        prototype = debug["prototype"]
        name = re.search(r"(\w+)\s*\(", prototype)
        ctype = structs.argument_type(prototype + " {", name.group(1), key[2]) if name else None
        if ctype is None:
            return None
        lines = [f"The game's own debug information gives {fn['name']}'s prototype: `{prototype}`. So {what} is "
                 f"`{ctype}`: the real type, no guess needed."]
        if (struct_name := structs.type_name(ctype)):
            header = (self._context(proj["id"], fn["unit"]) or {"header": ""})["header"]
            text = structs.definition(header, struct_name)
            lines += ["Its definition in context.h:", "```c", text, "```"] if text else \
                [f"{struct_name} isn't defined in context.h: see get_context."]
        return "\n".join(lines)

    def _struct_brief(self, fn, proj) -> list[str]:
        """The brief's lines: each pointer argument's likely layout in one line. None when the game's debug
        information gives the function's prototype (the brief shows it): those types are the real ones."""
        try:
            if "meta" in fn.keys() and json.loads(fn["meta"] or "{}").get("debug"):
                return []
            found = self.structs(proj)
            if found is None:
                return []
            return brief_lines(found, fn["vram"], fn["name"], self.symbols(proj["id"]).label)
        except Exception:  # a heuristic: it never stands between an agent and the function
            logging.getLogger("decompdecomp").exception("guessing struct layouts failed")
            return []
