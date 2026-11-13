"""Machine drafts of the game's data as typed C (docs/PORTING.md, phase 1): no agents, no tokens.

The bytes alone would match as `u32 D_x[n] = {...}`, but a port needs the types the game uses: read as bytes on a
little-endian PC, a word array gives them in the wrong order, and a pointer kept as a number loses half of itself
on a 64-bit one. The types are in the game's code: every load and store of a datum says how wide its field is,
whether it's signed or a float, and a field that holds an address is a pointer (structs.py, read by the
processor's `Arch.field_accesses`, the same facts guess_struct uses). A word whose value is the start of a known
symbol is a pointer to it.

A draft is written only when the code says what every byte is: each one a field the code reads or writes, a
pointer to a known symbol, or zero padding before an aligned field. A datum read two ways, or with bytes nothing
explains, is left to the data agents, with what the code does with it in their brief (`summary`). Every draft is
judged like an agent's C (data_units.compile_data) and kept only if it holds the game's bytes.
"""

from __future__ import annotations

import math
import re
import struct
import time

from .. import structs
from . import data_units

TYPES = {("s", 1): "s8", ("u", 1): "u8", ("s", 2): "s16", ("u", 2): "u16", ("s", 4): "s32", ("u", 4): "u32",
         ("s", 8): "s64", ("u", 8): "u64", ("f", 4): "f32", ("f", 8): "f64"}
POINTER = 4  # the consoles this reads have 32-bit pointers (structs.POINTER_BYTES)


class Draft:
    """A field of the draft: its offset, size, C type, and how to write its value."""

    def __init__(self, offset: int, size: int, ctype: str, pointer: bool = False):
        self.offset, self.size, self.ctype, self.pointer = offset, size, ctype, pointer


def _layout(net, proj, address: int):
    """The code's fields of the datum at `address` (structs.FieldGuess list), or None without a reader."""
    found = net.structs(proj)
    if found is None:
        return None
    type_id = found.type_of((structs.GLOBAL, address))
    return found.layout(type_id).fields if type_id is not None else []


def summary(net, proj, address: int) -> list[str]:
    """What the game's code does with a datum, for an agent's brief: `+0x4 s16 (3 functions)`, conflicts too."""
    fields = _layout(net, proj, address) or []
    out = []
    for f in fields:
        line = f"+0x{f.offset:X} {f.ctype()} ({len(f.functions)} function{'s' if len(f.functions) != 1 else ''})"
        if f.others:
            line += "; also read as " + ", ".join(f"{t} ({len(w)})" for t, w in f.others.items())
        out.append(line)
    return out


def _pointee(symbols, functions: set[int], value: int, low: int, high: int) -> str | None:
    """The C for an address the game keeps in a datum: a known symbol's start (or a place inside one)."""
    if not low <= value < high:
        return None
    if value in functions:
        return symbols.name_at(value)[0]
    name, offset = symbols.name_at(value)
    if symbols.by_addr.get(value - offset) is None and offset:
        return None  # inside nothing the project names: not known to be an address
    return f"&{name}" if not offset else f"(u8 *)&{name} + 0x{offset:X}"


def _literal(ctype: str, raw: bytes, order: str) -> str | None:
    if ctype in ("f32", "f64"):
        value = struct.unpack((">" if order == "big" else "<") + ("f" if ctype == "f32" else "d"), raw)[0]
        if not math.isfinite(value):
            return None
        text = f"{value:.9g}" if ctype == "f32" else f"{value:.17g}"
        if not re.search(r"[.e]", text):
            text += ".0"
        return text + ("f" if ctype == "f32" else "")
    signed = ctype.startswith("s")
    value = int.from_bytes(raw, order, signed=signed)
    if signed and value == -(1 << (len(raw) * 8 - 1)):
        return f"({value + 1} - 1)"  # the most negative value can't be written as a literal
    suffix = ("LL" if signed else "ULL") if len(raw) == 8 else "U" if not signed and len(raw) == 4 else ""
    return f"{value}{suffix}" if value >= 0 else f"{value}{suffix if suffix != 'ULL' else ''}"


def _align(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def typed(net, proj, address: int, span: int, raw: bytes) -> tuple[str | None, str]:
    """(C defining the datum, "") from the code's evidence, or (None, why not)."""
    symbols = net.symbols(proj["id"])
    name = symbols.name_at(address)[0]
    order = "big" if net.cfg(proj).get("big_endian", True) else "little"
    low, high = net.console(proj).ram
    functions = {s.address for s in symbols.by_name.values() if s.kind == "func"}
    fields = _layout(net, proj, address)
    if fields is None:
        return None, "this console's code isn't read for types yet"
    if any(f.others for f in fields):
        return None, "the code reads it two ways"
    if any((f.kind, f.size) not in TYPES and f.points_to is None for f in fields):
        return None, "a field of a kind drafts don't write (a vector)"
    fields = [f for f in fields if f.offset < span]
    words = [int.from_bytes(raw[i:i + POINTER], order) for i in range(0, span - span % POINTER, POINTER)]

    if not fields:
        # nothing the code reads directly: a table of pointers, if every word is one or empty
        if address % POINTER or span % POINTER or not words or not all(w == 0 or _pointee(
                symbols, functions, w, low, high) for w in words) or not any(words):
            return None, "the code's accesses don't say what it is"
        if all(w == 0 or w in functions for w in words):
            values = ", ".join(_pointee(symbols, functions, w, low, high) or "0" for w in words)
            return _source(f"void (*{name}[{len(words)}])(void)", f"{{ {values} }}", words, symbols, functions,
                           net, proj), ""
        values = ", ".join(_pointee(symbols, functions, w, low, high) or "0" for w in words)
        return _source(f"void *{name}[{len(words)}]", f"{{ {values} }}", words, symbols, functions, net, proj), ""

    # The element: the fields the code touches, padded to the largest one's alignment. An array of them when the
    # datum is several (indexed accesses read as the first element's).
    end = max(f.offset + f.size for f in fields)
    widest = max(f.size for f in fields)
    stride = _align(end, min(widest, 8))
    count = span // stride if span % stride == 0 else 1
    if count == 1 and not end <= span:
        return None, "its fields run past where the next datum starts"
    tail = span - stride * count if count > 1 else span - end
    if count == 1 and tail and (tail >= min(widest, 8) or any(raw[end:span])):
        return None, f"bytes +0x{end:X}..+0x{span:X} aren't read by the code"
    layout: list[Draft] = []
    at = 0
    for f in sorted(fields, key=lambda f: f.offset):
        if f.offset < at:
            return None, f"fields overlap at +0x{f.offset:X}"
        gap = f.offset - at
        if gap:
            if gap >= f.size or any(raw[e * stride + at:e * stride + f.offset] for e in range(count)):
                return None, f"bytes +0x{at:X}..+0x{f.offset:X} aren't read by the code"
        pointer = f.points_to is not None and f.size == POINTER
        values = [int.from_bytes(raw[e * stride + f.offset:e * stride + f.offset + f.size], order) for e in range(count)]
        if not pointer and f.size == POINTER and f.kind != "f" and any(values) and all(
                v == 0 or _pointee(symbols, functions, v, low, high) for v in values):
            pointer = True  # the game keeps known addresses in it
        if pointer and not all(v == 0 or _pointee(symbols, functions, v, low, high) for v in values):
            return None, f"the pointer at +0x{f.offset:X} holds addresses of nothing the project names"
        layout.append(Draft(f.offset, f.size, "void *" if pointer else TYPES[(f.kind, f.size)], pointer))
        at = f.offset + f.size

    def value(field: Draft, element: int) -> str | None:
        piece = raw[element * stride + field.offset:element * stride + field.offset + field.size]
        if field.pointer:
            v = int.from_bytes(piece, order)
            return _pointee(symbols, functions, v, low, high) or "0" if v == 0 or _pointee(
                symbols, functions, v, low, high) else None
        return _literal(field.ctype, piece, order)

    rows = []
    for e in range(count):
        items = [value(f, e) for f in layout]
        if any(i is None for i in items):
            return None, "a value can't be written exactly (not a finite number)"
        rows.append(items)
    pointer_words = [int.from_bytes(raw[e * stride + f.offset:e * stride + f.offset + 4], order)
                     for e in range(count) for f in layout if f.pointer]
    if len(layout) == 1 and layout[0].offset == 0 and stride == layout[0].size:
        ctype = layout[0].ctype
        declarator = f"{ctype} {name}" if count == 1 and span == layout[0].size else \
            f"{ctype} {name}[{count}]" if ctype != "void *" else f"void *{name}[{count}]"
        body = rows[0][0] if count == 1 and span == layout[0].size else "{ " + ", ".join(r[0] for r in rows) + " }"
        return _source(declarator, body, pointer_words, symbols, functions, net, proj), ""
    members = " ".join(f"{f.ctype}{'' if f.ctype.endswith('*') else ' '}f_{f.offset:03X};" for f in layout)
    body = "{ " + ", ".join("{ " + ", ".join(r) + " }" for r in rows) + " }" if count > 1 else \
        "{ " + ", ".join(rows[0]) + " }"
    declarator = f"struct {{ {members} }} {name}" + (f"[{count}]" if count > 1 else "")
    return _source(declarator, body, pointer_words, symbols, functions, net, proj), ""


def _source(declarator: str, body: str, words, symbols, functions, net, proj) -> str:
    """The definition, with declarations of the symbols its pointers name that the shared header doesn't."""
    context = net._context(proj["id"])
    header = context["header"] if context is not None else ""
    lines = []
    for w in sorted({w for w in words if w}):
        name = symbols.name_at(w)[0]
        if re.search(rf"\b{re.escape(name)}\b", header) or any(name in line for line in lines):
            continue
        lines.append(f"extern void {name}();" if w in functions else f"extern u8 {name}[];")
    return "\n".join(lines + [f"{declarator} = {body};"])


def run(net, slug: str, log=print) -> dict:
    """Draft every unit nobody holds, judge each, keep what matches: {"matched", "tried", "skipped", "reasons"}."""
    from . import data_agents
    with net.progress.view():
        proj = data_units._project(net, slug)
        order = data_agents.queue(net, proj)
        held = {r["address"] for r in data_units._store(net, proj).all(
            "SELECT address FROM data_units WHERE project_id=? AND agent_id IS NOT NULL AND lease>?", proj["id"],
            time.time())}
    read = data_units.reader(net, proj)
    counts = {"matched": 0, "tried": 0, "skipped": 0, "reasons": {}}
    progress = getattr(log, "progress", None)
    for i, address in enumerate(order):
        if progress:
            progress({"phase": "Drafting the game's data from its code", "done": i, "total": len(order)})
        if address in held:
            continue
        with net.progress.view():
            info = data_units.unit(net, slug, net.symbols(proj["id"]).name_at(address)[0])
            span = info["span"]
            raw = read(address, span) if span else None
            source, why = typed(net, proj, address, span, raw) if raw is not None else (None, "its end isn't known")
        if source is None:
            counts["skipped"] += 1
            counts["reasons"][why] = counts["reasons"].get(why, 0) + 1
            continue
        counts["tried"] += 1
        with net.progress.view():  # data units aren't in the saved progress: no sync before each compile
            result = data_units.compile_data(net, slug, info["name"], source)
        if result.exact:
            counts["matched"] += 1
        elif counts["tried"] - counts["matched"] <= 3:
            log(f"{info['name']}: the draft didn't match ({result.problems[0][:160] if result.problems else ''})")
    if progress:
        progress({"phase": "Drafting the game's data from its code", "done": len(order), "total": len(order)})
    log(f"{counts['matched']} of {len(order)} data units written as typed C; {counts['tried'] - counts['matched']} "
        f"drafts didn't match, {counts['skipped']} left to agents (the code doesn't say every byte's type)")
    return counts
