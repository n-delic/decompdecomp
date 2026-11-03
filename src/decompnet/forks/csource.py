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


ADDRESS_KEY = "// FUNCTION: 0x"  # find(text, address_key(va)): the definition annotated with the original's address
_ANNOTATION = re.compile(r"^[ \t]*//[ \t]*FUNCTION:[ \t]*0x([0-9a-fA-F]+)\b[^\n]*(?:\n|$)", re.M)


def address_key(addr: int) -> str:
    """A name for find() meaning the definition after a `// FUNCTION: 0x<address>` annotation (reccmp's form, which
    decompilations of PC and Xbox games put before each function: its own name can be anything)."""
    return f"{ADDRESS_KEY}{addr:X}"


def _block_end(clean: str, brace: int) -> int:
    depth, k = 0, brace
    while k < len(clean):
        depth += (clean[k] == "{") - (clean[k] == "}")
        k += 1
        if depth == 0:
            break
    return k


def _annotated(text: str, addr: int) -> tuple[int, int] | None:
    """The function definition after the annotation, before the next one: types and declarations the code needs
    may come in between (`struct CRigOwner { ... };`), and so may an inline helper (`static inline int Clamp(...)
    { ... }`), which is never the annotated function when another definition follows: the first that isn't inline,
    else the first."""
    found = list(_ANNOTATION.finditer(text))
    clean = None
    for n, m in enumerate(found):
        if int(m.group(1), 16) != addr:
            continue
        clean = clean if clean is not None else _blank(text)
        bound = found[n + 1].start() if n + 1 < len(found) else len(text)
        spans = list(_definitions(text, clean, m.end(), bound))
        own = [s for s in spans if not _INLINE_HEAD.search(clean[s[0]:s[2]])]
        if spans:
            return (own or spans)[0][:2]
    return None


_INLINE_HEAD = re.compile(r"\b(?:inline|__inline|__forceinline)\b")


def _definitions(text: str, clean: str, pos: int, bound: int):
    """Each function definition from `pos` to `bound`: (start, end, where its body's `{` is)."""
    while True:
        brace = clean.find("{", pos, bound)
        if brace < 0:
            return  # only declarations up to the next annotation
        head_at = max(clean.rfind(";", pos, brace), clean.rfind("}", pos, brace), pos - 1) + 1
        head = clean[head_at:brace]
        k = _block_end(clean, brace)
        pos = k
        if "(" not in head or re.match(r"\s*(?:typedef\s+)?(?:struct|class|union|enum|namespace)\b", head):
            continue  # a type (or a brace initialiser): past it
        i = head_at
        while i < brace:  # comments, blank and #pragma lines before the definition's first line
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            line = text[i:end].strip()
            if line and not line.startswith(("//", "#")):
                break
            i = end + 1
        if k < len(text) and text[k] == "\n":
            k += 1
        yield min(i, brace), k, brace


def defined_name(text: str, span: tuple[int, int]) -> str | None:
    """The name a definition gives its function (`CAIBehaviour::Update` for `void CAIBehaviour::Update(int)`)."""
    head = _blank(text[span[0]:span[1]]).split("(", 1)[0]
    m = re.search(r"((?:[\w~<>]+::)*~?\w+|(?:[\w<>]+::)*operator\s*\S+)\s*$", head)
    return m.group(1) if m else None


def _unbalanced(between: str) -> bool:
    """True when what lies between a parameter list and the next `{` closes a parenthesis it didn't open: the
    parameter list was inside a macro call (`HEADER_INLINE(int Clamp(int x))`), and the `{` is another function's.
    Qualifiers, `throw()` and a constructor's initialisers (`: m_a(0)`) open what they close. A `)` followed by a
    parameter list closes a declarator opened before the name: a function returning a pointer to a function,
    `int (*f(int a))(int b)`."""
    depth = 0
    for k, c in enumerate(between):
        depth += (c == "(") - (c == ")")
        if depth < 0:
            if not between[k + 1:].lstrip().startswith(("(", "[")):
                return True
            depth = 0
    return False


def find(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the definition of `name`, or of its asm stub; None when absent. `name` may be an
    address_key(): the definition annotated with that address."""
    if name.startswith(ADDRESS_KEY):
        return _annotated(text, int(name[len(ADDRESS_KEY):], 16))
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
                if _unbalanced(clean[j:brace]):  # inside a macro's parentheses: `MACRO(int f(int a))`, no body
                    j = len(clean)
                else:
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


_SCOPE_BLOCK = re.compile(r"(?<![\w:])(?:extern|namespace(?:\s+[\w:]+)?)\s*$")  # (`extern "C"`, blanked)


def at_file_scope(text: str, at: int) -> bool:
    """True when `at` isn't inside a block of code: only `namespace` and `extern "C"` blocks, which hold definitions,
    are open there. find() takes a call followed by a block (`y = f(x) * 2.0;` then `if (y) { ... }`) for a
    definition of `f`, and where one is looked for past the first, the next one may be such a call."""
    clean, opened, last = _blank(text[:at]), [], 0
    for m in re.finditer(r"[{};]", clean):
        if m.group() == "{":
            opened.append(bool(_SCOPE_BLOCK.search(clean[last:m.start()])))
        elif m.group() == "}" and opened:
            opened.pop()
        last = m.end()
    return all(opened)


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


def _when_defined(kind: str, cond: str, macro: str) -> bool | None:
    """Whether an `#if` line's first branch is compiled once `macro` is defined: only for a test of `macro` alone
    (`#ifdef M`, `#ifndef M`, `#if defined(M)`, `#if !defined M`); None for any other condition."""
    if kind in ("ifdef", "ifndef"):
        return None if cond != macro else kind == "ifdef"
    m = re.fullmatch(r"(!\s*)?defined\s*(?:\(\s*(\w+)\s*\)|(\w+))", cond)
    if kind == "if" and m and (m.group(2) or m.group(3)) == macro:
        return not m.group(1)
    return None


def guard_macro(directive: str) -> str | None:
    """The macro whose definition compiles a branch that starts with `directive` (hidden_by's): M for `#ifdef M`
    or `#if defined(M)`, else None."""
    m = _CONDITIONAL.match(_blank(directive))  # (the line as written: `#ifdef M // parked`)
    if m is None or m.group(1) not in ("if", "ifdef"):
        return None
    cond = m.group(2).strip()
    name = cond if m.group(1) == "ifdef" else next(iter(_IDENTIFIER.findall(cond.replace("defined", ""))), None)
    return name if name and _when_defined(m.group(1), cond, name) else None


def as_defined(text: str, macro: str) -> str:
    """The text as the build sees it once `macro` is defined, with only the conditionals that test `macro` alone
    resolved: their directive lines go, and so do the branches it leaves out (a stand-in under `#ifndef M`). The
    rest, other conditionals included, is untouched; so is the whole text if such a conditional has an #elif."""
    clean = _blank(text)
    events = {clean.count("\n", 0, m.start()): (m.group(1), m.group(2).strip()) for m in _CONDITIONAL.finditer(clean)}
    stack: list[bool | None] = []  # per open #if: whether its current branch is compiled (None: not about macro)
    out = []
    for n, line in enumerate(text.splitlines(keepends=True)):
        kind, cond = events.get(n, (None, ""))
        if kind in ("if", "ifdef", "ifndef"):
            stack.append(_when_defined(kind, cond, macro))
            if stack[-1] is not None:
                continue
        elif kind in ("elif", "else", "endif") and stack:
            mine = stack[-1] is not None
            if kind == "elif" and mine:
                return text
            if kind == "else" and mine:
                stack[-1] = not stack[-1]
            elif kind == "endif":
                stack.pop()
            if mine:
                continue
        if all(v is not False for v in stack):
            out.append(line)
    return "".join(out)
