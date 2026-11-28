"""Fakematches: C that reaches the game's bytes through a trick that steers the compiler, not through what the
original programmer wrote. A `volatile` local, an empty block, `do { } while (0)` in a function body, `x = x;`,
`+ 0`: each one moves a load, a register or a branch, and each one hides the real source behind it (usually a missing
helper or macro, a wrong type, or two variables merged into one). The judge still decides the bytes; this says
whether the C that made them can be read as the game's own source (guide("rules")).

The checks are deliberately narrow, so genuine original code passes them: `volatile` through a pointer (hardware
registers, data an interrupt handler shares), an empty loop body (`while (*s++) ;`), `case X: ;`, `for (;;)`, and
`do { } while (0)` inside a macro's definition. What no pattern can see (a dummy local that only burns a register, a
temporary with no meaning) is the guide's to teach.

Used where a match is announced (network.py, app/forkengine.py), in the polishing brief (app/readable.py), and by the
permuter (app/permute.py), whose rewrites may add such tricks: a variant that adds one isn't judged.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .forks.csource import _blank

SHOWN = 6  # findings listed in one note; the rest are counted


@dataclass(frozen=True)
class Finding:
    line: int  # 1-based, in the source given
    what: str  # the pattern, in a few words
    hides: str  # what it usually stands for: where to look for the real source


VOLATILE = ("`volatile` on a local", "a wrong type, an aliasing pointer or a missing call")
DO_WHILE_0 = ("`do { } while (0)` in a function body", "a macro the original called")
CONSTANT_IF = ("`if (0)` / `if (1)`", "a sequence point, a type width or a missing inline helper")
EMPTY_IF = ("an empty `if` or `else`", "a removed debug call, or a condition the original wrote differently")
EMPTY_BLOCK = ("an empty `{ }` block", "a scoped local, a macro's expansion or a removed debug call")
BARE_SEMICOLON = ("a bare `;` statement", "a macro's expansion or a removed debug call")
SELF_ASSIGN = ("a self-assignment `x = x;`", "a sequence point, a type width or a missing helper")
PLUS_ZERO = ("`+ 0`", "a type width, a struct member or a missing helper")
TIMES_ONE = ("`* 1`", "a type width, an array stride or a missing helper")
ASM = ("inline assembly", "C the original wrote: a match must come from C")

_PREPROCESSOR = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.M)
_DO_WHILE_0 = re.compile(r"\}\s*while\s*\(\s*0\s*\)")
_CONSTANT_IF = re.compile(r"\bif\s*\(\s*[01]\s*\)")
_IF = re.compile(r"\bif\s*\(")
_ELSE_EMPTY = re.compile(r"\belse\s*(?:\{\s*\}|;)")
_EMPTY_BLOCK = re.compile(r"\{\s*\}")
_SELF_ASSIGN = re.compile(r"(?:^|(?<=[;{}]))\s*([A-Za-z_]\w*)\s*=\s*\1\s*;")
_PLUS_ZERO = re.compile(r"(?<=[\w)\]])\s*\+\s*0(?![\w.])")
_TIMES_ONE = re.compile(r"(?<=[\w)\]])\s*\*\s*1(?![\w.])")
_ASM = re.compile(r"\b(?:asm|__asm|__asm__)\b")
# `register u32 x asm("r4")`: a register variable, genuine where the project uses them (guide("fork"))
_REGISTER_VARIABLE = re.compile(r"(?:asm|__asm__|__asm)\s*\(\s*\"\$?[a-z]{1,2}\d{0,2}\"\s*\)", re.I)
_VOLATILE = re.compile(r"\bvolatile\b")


def _without_preprocessor(clean: str) -> str:
    """`clean` with every preprocessor line (and its continuations) blanked: a macro's own `do { } while (0)` is the
    idiom, not a trick."""
    return _PREPROCESSOR.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), clean)


def _bodies(clean: str) -> list[tuple[int, int]]:
    """(start, end) of each function body's inside: a file-scope `{` right after a parameter list's `)` (or after
    K&R parameter declarations' `;`). Struct, union and enum bodies and initialisers aren't bodies."""
    spans, depth, start = [], 0, 0
    for i, c in enumerate(clean):
        if c == "{":
            if depth == 0:
                before = clean[:i].rstrip()
                before = re.sub(r"(?:\bconst|\bthrow\s*\([^)]*\)|\bnoexcept)\s*$", "", before).rstrip()
                start = i + 1 if before.endswith((")", ";")) else -1
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, i))
    return spans


def _close_paren(text: str, at: int) -> int:
    """The index just past the `)` that closes the `(` at `at` (len(text) when it isn't closed)."""
    depth = 0
    for i in range(at, len(text)):
        depth += (text[i] == "(") - (text[i] == ")")
        if depth == 0:
            return i + 1
    return len(text)


def _prev(text: str, at: int) -> str:
    """The last character before `at` that isn't white space ("" at the start)."""
    before = text[:at].rstrip()
    return before[-1] if before else ""


def _in_parens(body: str) -> list[int]:
    """For each position of `body`, how deep it is inside parentheses (a `for (;;)` header's `;` aren't statements)."""
    depth, out = 0, []
    for c in body:
        if c == ")":
            depth = max(0, depth - 1)
        out.append(depth)
        if c == "(":
            depth += 1
    return out


def _scan(body: str) -> list[tuple[int, tuple[str, str]]]:
    """(offset in `body`, kind) of each finding in one function body (comments, strings and preprocessor lines
    blanked)."""
    found = []
    for m in _VOLATILE.finditer(body):
        # `volatile u32 *reg`, `(volatile u8 *)0x...`: through a pointer, hardware or shared data, the genuine use
        rest = re.match(r"[^;=,)]*", body[m.end():]).group()
        follows = body[m.end() + len(rest):m.end() + len(rest) + 1]
        if "*" not in rest and follows != ")":
            found.append((m.start(), VOLATILE))
    found += [(m.start(), DO_WHILE_0) for m in _DO_WHILE_0.finditer(body)]
    found += [(m.start(), CONSTANT_IF) for m in _CONSTANT_IF.finditer(body)]
    for m in _IF.finditer(body):
        after = body[_close_paren(body, m.end() - 1):]
        if re.match(r"\s*(?:\{\s*\}|;)", after):
            found.append((m.start(), EMPTY_IF))
    found += [(m.start(), EMPTY_IF) for m in _ELSE_EMPTY.finditer(body)]
    for m in _EMPTY_BLOCK.finditer(body):
        # a statement of its own: after another statement or block (not a loop's or an if's body, an initialiser)
        if _prev(body, m.start()) in (";", "{", "}"):
            found.append((m.start(), EMPTY_BLOCK))
    depth = _in_parens(body)
    for i, c in enumerate(body):
        # `; ;` or `{ ;`, outside a for header; `case X: ;`, `label: ;` and `while (...) ;` are the language's own
        if c == ";" and depth[i] == 0 and _prev(body, i) in (";", "{"):
            found.append((i, BARE_SEMICOLON))
    found += [(m.start(1), SELF_ASSIGN) for m in _SELF_ASSIGN.finditer(body)]
    found += [(m.start(), PLUS_ZERO) for m in _PLUS_ZERO.finditer(body)]
    found += [(m.start(), TIMES_ONE) for m in _TIMES_ONE.finditer(body)]
    found += [(m.start(), ASM) for m in _ASM.finditer(body)]
    return found


def findings(source: str) -> list[Finding]:
    """The fakematch patterns in the function bodies of `source`, in order. Declarations, macros' definitions and
    data around them aren't looked at."""
    clean = _without_preprocessor(_blank(source))
    out = []
    for start, end in _bodies(clean):
        body = clean[start:end]
        for at, (what, hides) in _scan(body):
            if (what, hides) == ASM and _REGISTER_VARIABLE.match(source, start + at):
                continue
            out.append(Finding(source.count("\n", 0, start + at) + 1, what, hides))
    return sorted(set(out), key=lambda f: (f.line, f.what))


def added(base: str, variant: str) -> bool:
    """True when `variant` has a fakematch pattern `base` didn't have (more of one kind counts): a rewrite that
    steers the compiler with a trick rather than one that reads like the original."""
    had = Counter(f.what for f in findings(base))
    has = Counter(f.what for f in findings(variant))
    return any(n > had[what] for what, n in has.items())


def listing(found: list[Finding]) -> str:
    """The findings as lines for an agent: where, what, and what each usually hides."""
    lines = [f"- line {f.line}: {f.what}: usually {f.hides}" for f in found[:SHOWN]]
    if len(found) > SHOWN:
        lines.append(f"- and {len(found) - SHOWN} more")
    return "\n".join(lines)


def note(source: str, again: str) -> str:
    """What a match's result adds when its C has fakematch patterns ("" when it has none). `again`: how this
    project's agent replaces the matching C with the real form."""
    found = findings(source)
    if not found:
        return ""
    return ("\n\n⚠️ The bytes match, but the C reaches them through a trick that steers the compiler (a fakematch: "
            "guide(\"rules\")). Each one hides what the original programmer wrote:\n" + listing(found) +
            f"\nLook for the real form (a missing helper or macro, a wrong type, two variables merged into one). {again}")
