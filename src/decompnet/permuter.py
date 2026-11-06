"""Rewrites of C that keep its meaning but change what the compiler makes of it: statement and declaration order,
casts, comparison forms, increment styles, `register`. The permuter (app/permute.py) tries them on near misses; the
judge decides what matches, as for everything else."""

from __future__ import annotations

import random
import re

TYPES_32 = ["int", "unsigned", "unsigned int", "long", "unsigned long", "s32", "u32", "uInt", "uLong"]
CASTS = ["(int)", "(unsigned)", "(s32)", "(u32)", "(uInt)", "(uLong)", "(short)", "(unsigned short)"]
_DECL = re.compile(r"^(\s+)(register\s+)?((?:unsigned\s+|signed\s+)?\w+)(\s*\**\s*)(\w+)(\s*(?:=[^;]*)?;)\s*$")
_STMT = re.compile(r"^\s+[^\s{}#][^{}]*;\s*$")
# statements that transfer control (or open blocks) are never moved; one-line `if (c) x;` statements may be
_CONTROL = re.compile(r"\b(return|break|continue|goto|case|default)\b|^\s*(else|for|while|do|switch)\b")


def _body_bounds(lines: list[str]) -> tuple[int, int]:
    start = next((i for i, l in enumerate(lines) if "{" in l), 0)
    end = max((i for i, l in enumerate(lines) if l.strip().startswith("}")), default=len(lines) - 1)
    return start + 1, end


def _swap_statements(lines, rng):
    lo, hi = _body_bounds(lines)
    pairs = [i for i in range(lo, hi - 1) if _STMT.match(lines[i]) and _STMT.match(lines[i + 1])
             and not _CONTROL.search(lines[i] + lines[i + 1]) and not _DECL.match(lines[i])
             and lines[i][:len(lines[i]) - len(lines[i].lstrip())] == lines[i + 1][:len(lines[i + 1]) - len(lines[i + 1].lstrip())]]
    if not pairs:
        return None
    i = rng.choice(pairs)
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return lines


def _shuffle_declarations(lines, rng):
    lo, hi = _body_bounds(lines)
    run = []
    for i in range(lo, hi):
        if _DECL.match(lines[i]) and "=" not in lines[i]:
            run.append(i)
        elif run:
            break
    if len(run) < 2:
        return None
    a, b = rng.sample(run, 2)
    lines[a], lines[b] = lines[b], lines[a]
    return lines


def _retype_local(lines, rng):
    lo, hi = _body_bounds(lines)
    cands = [i for i in range(lo, hi) if (m := _DECL.match(lines[i])) and m.group(3) in TYPES_32 and not m.group(4).strip()]
    if not cands:
        return None
    i = rng.choice(cands)
    m = _DECL.match(lines[i])
    lines[i] = f"{m.group(1)}{m.group(2) or ''}{rng.choice([t for t in TYPES_32 if t != m.group(3)])}{m.group(4)}{m.group(5)}{m.group(6)}"
    return lines


def _toggle_register(lines, rng):
    lo, hi = _body_bounds(lines)
    cands = [i for i in range(lo, hi) if _DECL.match(lines[i])]
    if not cands:
        return None
    i = rng.choice(cands)
    m = _DECL.match(lines[i])
    reg = "" if m.group(2) else "register "
    lines[i] = f"{m.group(1)}{reg}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"
    return lines


_REWRITES = [
    (re.compile(r"\b(\w+)\+\+;"), lambda m: f"{m.group(1)} += 1;"),
    (re.compile(r"\b(\w+) \+= 1;"), lambda m: f"++{m.group(1)};"),
    (re.compile(r"\+\+(\w+);"), lambda m: f"{m.group(1)}++;"),
    (re.compile(r"\b(\w+)--;"), lambda m: f"{m.group(1)} -= 1;"),
    (re.compile(r"\b(\w+) -= 1;"), lambda m: f"{m.group(1)}--;"),
    (re.compile(r"\((\w+) < (\w+)\)"), lambda m: f"({m.group(2)} > {m.group(1)})"),
    (re.compile(r"\((\w+) > (\w+)\)"), lambda m: f"({m.group(2)} < {m.group(1)})"),
    (re.compile(r"\((\w+) <= (\w+)\)"), lambda m: f"({m.group(2)} >= {m.group(1)})"),
    (re.compile(r"\((\w+) >= (\w+)\)"), lambda m: f"({m.group(2)} <= {m.group(1)})"),
    (re.compile(r"\(!(\w+)\)"), lambda m: f"({m.group(1)} == 0)"),
    (re.compile(r"\((\w+) == 0\)"), lambda m: f"(!{m.group(1)})"),
    (re.compile(r"\((\w+) != 0\)"), lambda m: f"({m.group(1)})"),
    (re.compile(r"\(\s*(?:unsigned short|unsigned|short|int|s32|u32|uInt|uLong)\s*\)\s*(?=\w)"), lambda m: ""),
]


def _rewrite(lines, rng):
    lo, hi = _body_bounds(lines)
    options = [(i, rx, fn) for i in range(lo, hi) for rx, fn in _REWRITES if rx.search(lines[i])]
    if not options:
        return None
    i, rx, fn = rng.choice(options)
    matches = list(rx.finditer(lines[i]))
    m = rng.choice(matches)
    lines[i] = lines[i][:m.start()] + fn(m) + lines[i][m.end():]
    return lines


def _add_cast(lines, rng):
    lo, hi = _body_bounds(lines)
    cands = [(i, m) for i in range(lo, hi) if not _DECL.match(lines[i]) and "=" in lines[i]
             # rvalues only: never cast an assignment target, a call, or the base of a member/array access
             for m in re.finditer(r"(?<=[=(,+\-*/&|^ ])(\w+)\b(?!\s*(?:\(|=(?!=)|\+\+|--|\[|\.|->|[-+*/&|^]=))",
                                  lines[i]) if not m.group(1).isdigit()]
    if not cands:
        return None
    i, m = rng.choice(cands)
    lines[i] = lines[i][:m.start()] + rng.choice(CASTS) + lines[i][m.start():]
    return lines


MUTATIONS = [_swap_statements, _shuffle_declarations, _retype_local, _toggle_register, _rewrite, _rewrite, _add_cast]


def mutate(source: str, rng: random.Random, focus: str = "") -> str | None:
    lines = source.split("\n")
    preferred = {"stack": [_shuffle_declarations, _toggle_register],
                 "register": [_swap_statements, _shuffle_declarations],
                 "call": [_retype_local, _add_cast], "constant": [_rewrite, _add_cast],
                 "control": [_rewrite, _swap_statements]}.get(focus, [])
    for _ in range(6):
        out = rng.choice(MUTATIONS + preferred * 2)(list(lines), rng)
        if out is not None and out != lines:
            return "\n".join(out)
    return None
