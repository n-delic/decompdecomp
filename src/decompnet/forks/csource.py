"""Find and replace one function in a C or C++ source file.

Handles real definitions (including K&R and C++ `Class::method` forms) and the stubs decomp projects use for
functions that aren't decompiled yet: `INCLUDE_ASM(...)`, `GLOBAL_ASM(...)` / `#pragma GLOBAL_ASM(...)`.
"""

from __future__ import annotations

import re

_COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)


def _blank(text: str) -> str:
    """Same length, with comments and strings blanked, so braces inside them don't count."""
    return _COMMENT_OR_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def _stub(text: str, name: str) -> tuple[int, int] | None:
    pat = re.compile(rf"^[ \t]*(?:#\s*pragma\s+)?(?:INCLUDE_ASM|GLOBAL_ASM|INCLUDE_RODATA_FN)\s*\((?:[^;\n]*[\"/,\s]){re.escape(name)}(?:\.s)?[\"\s]*\)\s*;?[ \t]*\n?", re.M)
    m = pat.search(text)
    return (m.start(), m.end()) if m else None


def is_stub(code: str) -> bool:
    """True for an asm stub (INCLUDE_ASM / GLOBAL_ASM line) rather than a C definition."""
    return bool(re.match(r"\s*(?:#\s*pragma\s+)?(?:INCLUDE_ASM|GLOBAL_ASM|INCLUDE_RODATA_FN)\s*\(", code))


def find(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the definition of `name`, or of its asm stub; None when absent."""
    clean = _blank(text)
    simple = name.split("::")[-1]
    if "::" in name:  # Class::method: match it exactly
        pat = re.compile(rf"(?<![\w:~]){re.escape(name)}\s*\(")
    else:  # plain name: also match a definition written as Class::name
        pat = re.compile(rf"(?<![\w:~])(?:[\w~<>]+::)*{re.escape(name)}\s*\(")
    for m in pat.finditer(clean):
        # walk to the end of the parameter list
        depth, i = 0, m.end() - 1
        while i < len(clean):
            c = clean[i]
            depth += (c == "(") - (c == ")")
            i += 1
            if depth == 0:
                break
        # a definition continues with optional qualifiers (const, throw()) or K&R parameter declarations, then '{'
        j = i
        while j < len(clean) and clean[j].isspace():
            j += 1
        if j < len(clean) and clean[j] == ";":
            continue  # a prototype or a call
        while j < len(clean) and clean[j] != "{":
            nxt = clean.find(";", j)
            brace = clean.find("{", j)
            if brace == -1:
                j = len(clean)
                break
            if nxt == -1 or brace < nxt:
                j = brace
                break
            if any(c in clean[j:nxt] for c in "(){}="):
                j = len(clean)
                break
            j = nxt + 1  # one K&R parameter declaration
        if j >= len(clean) or clean[j] != "{":
            continue
        line_start = clean.rfind("\n", 0, m.start()) + 1
        if clean[line_start:m.start()].strip().startswith(("#", "return", "if", "else")):
            continue
        # include the return type / storage class on preceding lines that belong to this declaration
        start = line_start
        prev_end = start - 1
        while prev_end > 0:
            prev_start = clean.rfind("\n", 0, prev_end) + 1
            prev = clean[prev_start:prev_end].strip()
            if prev and not prev.endswith((";", "}", "{", ")")) and not prev.startswith("#") and re.fullmatch(r"[\w\s\*&:<>,]+", prev):
                start = prev_start
                prev_end = prev_start - 1
            else:
                break
        depth, k = 0, j
        while k < len(clean):
            depth += (clean[k] == "{") - (clean[k] == "}")
            k += 1
            if depth == 0:
                break
        if k < len(text) and text[k] == "\n":
            k += 1
        return start, k
    return _stub(text, simple)


# a C++ constructor or destructor defined outside its class: CFoo::CFoo(, Outer::CFoo::~CFoo(
_STRUCTOR = re.compile(r"(?<![\w:~])((?:\w+::)*?(\w+))::(~?)\2\s*\(")


def structor(text: str) -> str | None:
    """The C++ constructor or destructor `text` defines (`CFoo::CFoo`, `CFoo::~CFoo`), when it defines exactly one: a
    PC game's function an agent writes as one is named after it (ForkEngine._attempt)."""
    found = {f"{m.group(1)}::{m.group(3)}{m.group(2)}" for m in _STRUCTOR.finditer(_blank(text))}
    defined = [n for n in found if find(text, n) and not is_stub(get(text, n) or "")]
    return defined[0] if len(defined) == 1 else None


# reccmp's annotations (`// FUNCTION: 0x...`, `// GLOBAL:`, `// STUB:`) say which of the original's functions a
# definition is: the project's markers, not a description of it. Only reccmp's own words: any `// WORD:` took
# `// NOTE:` and `// TODO:` for one, and a polished comment that had one was dropped
_MARKER = re.compile(r"//\s*(?:FUNCTION|STUB|TEMPLATE|SYNTHETIC|LIBRARY|GLOBAL|VTABLE|STRING|LINE):")


def comment_above(text: str, start: int) -> int:
    """Where the comment right above a definition (at `start`, a line's start) begins: its `//` lines and `/* */`
    blocks, with no blank line between, up to a marker such as `// FUNCTION:`; `start` when there's none. A polished
    function's comment saying what it does belongs to it (a project's file kept only the definition)."""
    at = start
    while at > 0:
        prev = text.rfind("\n", 0, at - 1) + 1
        line = text[prev:at].strip()
        if line.startswith("//") and not _MARKER.match(line):
            at = prev
        elif line.endswith("*/") and (opens := text.rfind("/*", 0, at)) >= 0 \
                and not text[text.rfind("\n", 0, opens) + 1:opens].strip():
            at = text.rfind("\n", 0, opens) + 1
        else:
            break
    return at


def marker_above(text: str, at: int) -> int:
    """Where the reccmp annotation on the line right above `at` (a line's start) begins, or `at` without one."""
    prev = text.rfind("\n", 0, at - 1) + 1 if at > 0 else 0
    return prev if at > 0 and _MARKER.match(text[prev:at].strip()) else at


def get(text: str, name: str) -> str | None:
    span = find(text, name)
    return text[span[0]:span[1]] if span else None


def replace(text: str, name: str, code: str) -> str:
    """Put `code` in place of the function (or its stub); append it when the function isn't there yet."""
    code = code.strip("\n") + "\n"
    span = find(text, name)
    if span is None:
        return text.rstrip("\n") + "\n\n" + code
    return text[:span[0]] + code + text[span[1]:]


# ---- which `#if` branch the build leaves out
# An x86 game's agents kept drafts inside `#if defined(SOME_FLAG)` blocks no build defines: the brief showed them as
# the function's current source, every compile replaced them there, and the judge scored other code.

_CONDITIONAL = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b(.*)$", re.M)
_COND_TOKEN = re.compile(r"\s*(defined\b|[A-Za-z_]\w*|\d+[uUlL]*|&&|\|\||!|\(|\))")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def _conditionals(text: str, pos: int):
    """(line number, directive, its condition) of each conditional line above `pos`, comments left out."""
    clean = _blank(text[:pos])
    for m in _CONDITIONAL.finditer(clean):
        yield clean.count("\n", 0, m.start()) + 1, m.group(1), m.group(2).strip()


def condition_names(text: str, pos: int) -> set[str]:
    """The macros the conditional lines above `pos` test (what hidden_by needs to know about)."""
    return {n for _, kind, cond in _conditionals(text, pos) if kind not in ("else", "endif")
            for n in _IDENTIFIER.findall(cond) if n != "defined"}


def _either(a: bool | None, b: bool | None) -> bool | None:
    return True if a or b else None if a is None or b is None else False


def _both(a: bool | None, b: bool | None) -> bool | None:
    return False if a is False or b is False else None if a is None or b is None else True


def _negated(a: bool | None) -> bool | None:
    return None if a is None else not a


def _condition(cond: str, undefined: set[str]) -> bool | None:
    """An #if's condition: True, False, or None when it can't be told (a macro that may be defined, arithmetic)."""
    tokens, at = [], 0
    while at < len(cond):
        m = _COND_TOKEN.match(cond, at)
        if not m:
            return None if cond[at:].strip() else _parse(tokens, undefined)
        tokens.append(m.group(1))
        at = m.end()
    return _parse(tokens, undefined)


def _parse(tokens: list[str], undefined: set[str]) -> bool | None:
    pos = 0

    def take(t: str) -> bool:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] == t:
            pos += 1
            return True
        return False

    def atom() -> bool | None:
        nonlocal pos
        if take("!"):
            return _negated(atom())
        if take("("):
            v = either()
            return v if take(")") else None
        if pos >= len(tokens):
            return None
        tok = tokens[pos]
        pos += 1
        if tok == "defined":
            paren = take("(")
            if pos >= len(tokens) or not _IDENTIFIER.fullmatch(tokens[pos]):
                return None
            name = tokens[pos]
            pos += 1
            if paren and not take(")"):
                return None
            return False if name in undefined else None
        if tok[0].isdigit():
            return int(tok.rstrip("uUlL")) != 0
        return False if tok in undefined else None  # a defined macro's value isn't known

    def both() -> bool | None:
        v = atom()
        while take("&&"):
            v = _both(v, atom())
        return v

    def either() -> bool | None:
        v = both()
        while take("||"):
            v = _either(v, both())
        return v

    value = either()
    return value if pos == len(tokens) else None


def hidden_by(text: str, pos: int, undefined: set[str]) -> tuple[int, str] | None:
    """(line number, the directive) of the conditional branch that leaves the code at `pos` out of the build, or None
    when it's compiled or that can't be told. `undefined`: the macros known never to be defined (ForkProject.
    never_defined); any other macro, the compiler's own included, could be, so a test of one hides nothing."""
    stack: list[list] = []  # per open #if: [line, directive, this branch's value, whether a branch before was taken]
    lines = text.splitlines()
    for n, kind, cond in _conditionals(text, pos):
        if kind in ("if", "ifdef", "ifndef"):
            v = (_condition(cond, undefined) if kind == "if" else
                 (False if cond in undefined else None) if kind == "ifdef" else (True if cond in undefined else None))
            stack.append([n, v, v])
        elif kind == "elif" and stack:
            taken = stack[-1][2]
            v = _both(_negated(taken), _condition(cond, undefined))
            stack[-1] = [n, v, _either(taken, v)]
        elif kind == "else" and stack:
            stack[-1] = [n, _negated(stack[-1][2]), True]
        elif kind == "endif" and stack:
            stack.pop()
    for n, value, _ in stack:
        if value is False:
            return n, lines[n - 1].strip()
    return None
