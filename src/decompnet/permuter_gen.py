"""Variants of one function's C, made by decomp-permuter's randomizer (MIT, a pinned tool: toolchains.toml).

A program of its own, not a module of decompdecomp: app/permute.py runs it with the Python of decomp-permuter's
environment, `python -I permuter_gen.py <decomp-permuter's folder>`, and it imports nothing of decompdecomp. Our
own judge scores what it writes; this only makes candidates, so a variant that doesn't compile costs one compile.

In, on stdin, one job per line, each a JSON object:
    header    the shared header, as the compiler gets it
    source    the near miss's C
    function  its name
    family    base, ido, gcc or mwcc: the weights decomp-permuter tuned per compiler (default_weights.toml)
    seed      any number: the same job gives the same variants
    count     how many variants to write
Out, on stdout, one JSON object per line: {"variant": C} for each variant, distinct and none the source itself,
each the function alone plus whatever the randomizer added beside it (a helper it inlined code into); or
{"error": why} when the C can't be read; then {"done": true}, and the next job. It runs for as long as its stdin
is open, so a header is parsed once per C a turn starts from: a second for a header of 300 KB.

decomp-permuter reads C through pycparser, which knows neither comments nor the preprocessor: comments go, and so
do the header's preprocessor lines (as for m2c's context, seeds.py), and the source's, which each variant gets back
at its top. A macro the code calls like a function stays a call (the compiler still gets it); one called with a
type (`F(int, 0x20)`), and every object-like one, the header's and the source's own, is put in where it's used
(`expand_in_source`), as pycparser can't read those as they are. The header's object-like macros are put in
where it uses them, MSVC's words are rewritten (`msvc_as_c`; in the source, `Restorer` puts them back in each
variant), and a header written for C++ is read as the C of its types (`cxx_as_c`, `type_names`). C++ in the
function itself (a method, a constructor call, a template) still isn't read.

The walk is decomp-permuter's own: a variant is the last one rewritten once more (KEEP of the time) or the source
rewritten afresh, so variants drift a few rewrites away from the source and come back.
"""

import json
import random
import re
import sys

KEEP = 0.6  # decomp-permuter's default chance of building on the last variant instead of starting again
SOURCE_STARTS = "DECOMPDECOMP_SOURCE_STARTS"  # a typedef put between the header and the source, to find the seam
TRIES_PER_VARIANT = 4  # rewrites can repeat or fail: give up on a job that yields nothing new this many times over
MAX_SEED = 10**20
# a comment, or a string or character literal (group 1), which keeps any /* inside it
_COMMENT = re.compile(r'//[^\n]*|/\*.*?\*/|("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')', re.S)


def plain_c(text: str, directives: bool = True) -> str:
    """`text` without comments and continued lines, and without preprocessor lines unless `directives`."""
    text = _COMMENT.sub(lambda m: m.group(1) or " ", text.replace("\\\n", ""))
    return text if directives else "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# ---------------------------------------------------------------- MSVC's words, and a C++ header read as C
# pycparser reads C99. A PC or Xbox game's code is MSVC's: `__int64`, calling conventions and `__declspec` in C, and
# headers written for C++. An x86 game's shared header had `typedef unsigned __int64 ...;`, so decomp-permuter read
# not one of its near misses (October 2026: 126 of 127 turns fell back to the simple rewrites).

INT_TYPES = {"8": "char", "16": "short", "32": "int", "64": "long long"}
# __intN as a stand-in type name: a variant keeps the stand-in, which goes back to MSVC's words (an old MSVC has no
# `long long`)
STAND_INS = "".join(f"typedef {t} DD_int{n};\ntypedef unsigned {t} DD_uint{n};\n" for n, t in INT_TYPES.items())
_MSVC_INT = re.compile(r"\b(?:(unsigned)\s+|signed\s+)?__int(8|16|32|64)\b")
_MSVC_SUFFIX = re.compile(r"\b(\d[0-9A-Fa-fxX]*?)(u?)i64\b", re.I)  # 0x...ui64: C's ULL
_C_SUFFIX = re.compile(r"\b(\d[0-9A-Fa-fxX]*?)(u?)ll\b", re.I)
_STAND_IN = re.compile(r"\bDD_(u?)int(8|16|32|64)\b")
_DECLSPEC = r"__declspec\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
_CALLCONV = re.compile(r"\b(?:_{1,2}(?:cdecl|stdcall|fastcall|thiscall|clrcall|vectorcall)|WINAPI|CALLBACK|APIENTRY)"
                       r"\b(?=(\s*(?:\*\s*)*)([A-Za-z_]\w*))")
_DECLSPEC_AT = re.compile(rf"\b{_DECLSPEC}\s*(?=[\w\s*]*?\b([A-Za-z_]\w*)\s*[;(=\[{{,])")
_STARS = r"(?:\*\s*)*"
_EXTERN_C_AT = re.compile(r"\bextern\s+\"C\"\s*(?=[\w\s*]*?\b([A-Za-z_]\w*)\s*[;(=\[,])")
_INLINE = re.compile(r"\b__(?:force)?inline\b")
_NOTHING = re.compile(rf"\b(?:__w64|__unaligned|__ptr32|__ptr64|__restrict|{_DECLSPEC})\s*|\b(?:_{{1,2}}(?:cdecl|stdcall"
                      r"|fastcall|thiscall|clrcall|vectorcall)|WINAPI|CALLBACK|APIENTRY)\b\s*")


def msvc_as_c(text: str) -> str:
    """`text` with MSVC's words that pycparser can't read rewritten as C it reads, for good: the header, which the
    permuter reads but never writes."""
    text = _MSVC_INT.sub(lambda m: f"DD_{'u' if m.group(1) else ''}int{m.group(2)}", text)
    text = _MSVC_SUFFIX.sub(lambda m: f"{m.group(1)}{m.group(2).upper()}LL", text)
    return _NOTHING.sub("", _INLINE.sub("inline", text))


# C++ in a function's own C, each as a name or a call pycparser reads, and back (Restorer): `A::B` and `A::~A`, a
# template's arguments (`RawAt<float>(p, 4)`, `static_cast<int>(x)`), a reference (`const T &a`), a local built by
# its constructor (`T t(a, b);`) and a temporary (`T(a, b)`)
_SCOPE, _DTOR = "__DDq__", "DDdtor_"
_QUALIFIED = re.compile(r"(?<![\w:])((?:[A-Za-z_]\w*::)+~?[A-Za-z_]\w*)")
_TEMPLATE_ARGS = r"(?:[\w\s*&,:]|<[\w\s*&,:]*>)*"
_TEMPLATE_ID = re.compile(rf"\b([A-Za-z_]\w*)\s*<({_TEMPLATE_ARGS})>(?=\s*[(:])")
_TEMPLATE_BACK = re.compile(r"\bDDt(\d+)_\w*")
_CONST_METHOD = re.compile(r"\)\s*const(\s*)(?=\{)")
C_TYPE_WORDS = frozenset("void char short int long float double signed unsigned _Bool".split())


def _body_depths(text: str) -> list[int]:
    """The brace depth at each character of `text` (comments and strings already gone, plain_c)."""
    depths, depth = [], 0
    for ch in text:
        if ch == "}":
            depth -= 1
        depths.append(depth)
        if ch == "{":
            depth += 1
    return depths


class Restorer:
    """The source as pycparser reads it, and each variant back as the source wrote it.

    MSVC's words: `__int64` as its stand-in, and the calling conventions and `__declspec`s noted with the name each
    was on and put back in front of it (the first time a variant names it: its declaration, as the source's order is
    kept). C++ (most of an x86 game's near misses are methods): what pycparser can't read becomes a name or a call it
    can (`_SCOPE`, `DDt<n>_`, `DDref_`, `DDctor`, `DDnew_`), which the randomizer moves about like any other, and goes
    back as it was; `types` are the names the header has for types, which tell `T t(a);` from a call. Anything the
    randomizer moved or lost costs a compile or a worse score, never a wrong match: the judge scores what the
    compiler makes of the variant."""

    def __init__(self, source: str, types=frozenset()):
        # (the words, the name, where: right before the "name", before the "stars" of a pointer to it, on its "line")
        self.placed: list[tuple[str, str, str]] = []
        for m in _CALLCONV.finditer(source):  # (before a pointer's `*` only where it was: `T *__stdcall f(` returns one)
            self.placed.append((m.group(0), m.group(2), "stars" if "*" in m.group(1) else "name"))
        for m in [*_DECLSPEC_AT.finditer(source), *_EXTERN_C_AT.finditer(source)]:
            self.placed.append((m.group(0).strip(), m.group(1), "line"))
        found = _INLINE.search(source)
        self.inline = found.group(0) if found else None
        self.suffixes = bool(_MSVC_SUFFIX.search(source))
        self.templates: list[str] = []
        self.const_methods: list[str] = []
        self.source = self._cxx_as_c(_EXTERN_C_AT.sub("", msvc_as_c(source)), set(types) | C_TYPE_WORDS)

    def name(self, function: str) -> str:
        """`function` as the source pycparser reads names it (`A::f`: A__DDq__f)."""
        return function.replace("::", _SCOPE).replace("~", _DTOR)

    def _cxx_as_c(self, text: str, types: set[str]) -> str:
        def template(m: re.Match) -> str:
            self.templates.append(m.group(0))
            return f"DDt{len(self.templates) - 1}_{m.group(1)}"

        text = _TEMPLATE_ID.sub(template, text)
        for m in re.finditer(rf"((?:[A-Za-z_]\w*::)*~?[A-Za-z_]\w*)\s*\([^{{;]*?{_CONST_METHOD.pattern}", text):
            self.const_methods.append(self.name(m.group(1)))
        text = _CONST_METHOD.sub(r")\1", text)
        text = _QUALIFIED.sub(lambda m: self.name(m.group(1)), text)
        typed = "|".join(sorted(map(re.escape, types), key=len, reverse=True))
        # a reference, where only a declaration can have it: after a type, before the name it declares
        text = re.sub(rf"\b((?:{typed})\s*(?:\bconst\b\s*)?)&\s*([A-Za-z_]\w*)(?=\s*[,)=;\[])", r"\1*DDref_\2", text)
        depths = _body_depths(text)
        out, last = [], 0
        # in a body: `T t(args);` builds a local, `T(args)` is a temporary (sizeof(T), (T *) and `T x` aren't these)
        for m in re.finditer(rf"(?<![\w.>])({typed})(\s+[A-Za-z_]\w*)?\s*\(", text):
            if depths[m.start()] < 1 or m.group(1) in C_TYPE_WORDS and not m.group(2):
                continue
            head = text[max(0, m.start() - 1):m.start()].strip()
            if m.group(2) and head not in ("", ";", "{", "}"):
                continue
            out.append(text[last:m.start()])
            out.append(f"{m.group(1)}{m.group(2)} = DDctor(" if m.group(2) else f"DDnew_{m.group(1)}(")
            last = m.end()
        return "".join(out) + text[last:]

    def _cxx_back(self, variant: str) -> str:
        variant = re.sub(r"\b([A-Za-z_]\w*)\s*=\s*DDctor\(", r"\1(", variant)
        variant = re.sub(r"\bDDnew_(\w+?)\(", r"\1(", variant)
        variant = re.sub(r"\*\s*DDref_(\w+)", r"&\1", variant)
        for name in self.const_methods:
            m = re.search(rf"\b{re.escape(name)}\s*\(", variant)
            if m:
                depth, i = 0, m.end() - 1
                for i in range(m.end() - 1, len(variant)):
                    depth += {"(": 1, ")": -1}.get(variant[i], 0)
                    if depth == 0:
                        break
                variant = f"{variant[:i + 1]} const{variant[i + 1:]}"
        variant = variant.replace(_SCOPE, "::").replace(_DTOR, "~")
        return _TEMPLATE_BACK.sub(lambda m: self.templates[int(m.group(1))], variant)

    def restore(self, variant: str) -> str:
        variant = self._cxx_back(variant)
        variant = _STAND_IN.sub(lambda m: f"{'unsigned ' if m.group(1) else ''}__int{m.group(2)}", variant)
        if self.suffixes:
            variant = _C_SUFFIX.sub(lambda m: f"{m.group(1)}{m.group(2).lower()}i64", variant)
        if self.inline:
            variant = re.sub(r"\binline\b", self.inline, variant)
        for words, name, where in self.placed:
            m = re.search(rf"{_STARS if where == 'stars' else ''}\b{re.escape(name)}\b", variant)
            if not m:
                continue
            at = m.start()
            if where == "line":  # a __declspec: in front of its declaration, or of a struct's name (its alignment)
                line = variant.rfind("\n", 0, at) + 1
                lead = variant[line:at]
                if not re.search(r"\b(?:struct|union|class)\s+$", lead):
                    at = line + len(lead) - len(lead.lstrip())
            variant = f"{variant[:at]}{words} {variant[at:]}"
        return variant


_ACCESS = re.compile(r"\b(?:public|private|protected)\s*:(?!:)")
_DROPPED_WORDS = re.compile(r"\b(?:explicit|mutable|virtual)\b\s*|\bextern\s+\"C(?:\+\+)?\"\s*(?!\{)")
_TAG = re.compile(r"\b(struct|union|class|enum)\s+([A-Za-z_]\w*)")
_DEFAULT_ARG = re.compile(r"\s*=\s*(?:[^,()]|\([^()]*\))+(?=[,)])")
_REFERENCE = re.compile(r"(?<=[\w*>\s])&(?=\s*(?:const\b\s*)?[A-Za-z_)\[,])")
CXX_TYPES = {"bool": "_Bool", "wchar_t": "unsigned short"}


def _segments(text: str):
    """`text`'s declarations at its top level, in order, each (head, body): body is what's between the braces of a
    function's, namespace's or `extern "C"`'s block (the head then ends before it), else None and head is the whole
    declaration with its `;` (a struct's braces in it)."""
    i, start, depth = 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            yield text[start:i + 1], None
            start = i + 1
        elif ch == "{" and depth == 0:
            close, level = i, 0
            for close in range(i, len(text)):
                level += {"{": 1, "}": -1}.get(text[close], 0)
                if level == 0:
                    break
            head = text[start:i]
            block = re.search(r"\)\s*(?:const\s*)?(?::[^{]*)?$", head) and not re.search(r"\b(?:struct|union|class|enum)\b[^()]*$", head)
            if block or re.match(r"\s*(?:namespace\b|extern\s+\"C)", head):
                yield head, text[i + 1:close]
                start = i = close + 1
                continue
            i = close  # a struct, union, enum or initializer: its declaration goes on to its `;`
        i += 1
    if text[start:].strip():
        yield text[start:], None


_DIRECT_INIT = re.compile(r"\w\s*\(\s*[-+&\"'.\d]")  # a constructor's arguments, not parameters


def _without_initializer(head: str) -> str:
    """A declaration without its initial value (`= CVec2(48.0f, 192.0f)` is C++; the permuter needs the type only),
    when the `=` comes before any brace of its own (an enum's or struct's values stay)."""
    depth = 0
    for i, ch in enumerate(head):
        if ch in "([{":
            if ch == "{":
                return head
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "=" and depth == 0:
            return head[:i].rstrip() + ";"
    return head


def _member_function(member: str) -> bool:
    """A struct member that is a function (a C++ method, constructor or operator), not a pointer to one."""
    m = re.search(r"\(", member)
    return bool(m) and not re.match(r"\s*\*", member[m.end():]) or bool(re.search(r"\boperator\b", member))


def _struct_as_c(head: str) -> str:
    """A declaration with a struct (or class) in it, as C: its members only, with no base classes or methods."""
    open_ = head.find("{")
    close = head.rfind("}")
    if open_ < 0 or close < open_:
        return head
    before = re.sub(r"(\b(?:struct|union|class)\s+\w+)\s*:[^{]*$", r"\1 ", head[:open_])
    members = []
    for member, body in _segments(_ACCESS.sub("", head[open_ + 1:close])):
        if body is not None or not member.strip() or _member_function(member):
            continue
        if re.match(r"\s*(?:static|typedef|friend|using|template)\b", member):
            continue
        members.append(_struct_as_c(member) if "{" in member else member)
    return f"{before}{{{''.join(members)}\n}}{head[close + 1:]}"


def cxx_as_c(header: str) -> str:
    """A header written for C++ as decomp-permuter can read it, for its types alone: a class is a struct of its
    data members (no methods, base classes or access words), every struct, union and enum is a type by its own name
    as in C++, references are pointers, and what C has no form for (templates, out-of-class members, operators)
    is left out. The permuter never writes the header, so this only has to parse."""
    out = []

    def walk(text: str) -> None:
        for head, body in _segments(_DROPPED_WORDS.sub("", text)):
            stripped = head.strip()
            if re.match(r"(?:namespace\b|extern\s+\"C)", stripped):
                walk(body or "")
            elif re.match(r"(?:template|using|namespace)\b", stripped) or (
                    "{" not in head and ("::" in head or re.search(r"\boperator\b", head))):
                continue
            elif body is not None:  # a function defined here: its prototype is all the permuter needs
                out.append(_DEFAULT_ARG.sub("", re.sub(r"\)\s*(?:const\s*)?:[^{]*$", ")", head)).rstrip() + ";")
            else:
                head = _without_initializer(head)
                if _DIRECT_INIT.search(head) and "{" not in head:  # C++'s `T g(1.0f, 2.0f);`
                    continue
                if "{" in head:
                    out.append(_struct_as_c(head))
                else:
                    out.append(_DEFAULT_ARG.sub("", head) if "(" in head else head)

    walk(header)
    return _REFERENCE.sub("*", re.sub(r"\bclass\b", "struct", "\n".join(out)))


_OBJECT_MACRO = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]*(.*)$", re.M)
_C_WORDS = frozenset("""auto break case char const continue default do double else enum extern float for goto if
inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile
while _Bool _Complex""".split())
# an undeclared name where only a type can be: before a declarator, at the start of a declaration or a parameter
_TYPE_SPOT = re.compile(r"(?:^|(?<=[;{}(,])|\b(?:const|volatile|static|extern|inline|register)\b)\s*([A-Za-z_]\w*)\b"
                        r"(?=\s*(?:(?:\*\s*|\bconst\b\s*)*[A-Za-z_]\w*\s*[;,()\[=:]|\(\s*\*))", re.M)
_PARAMETERS = re.compile(r"\w\s*\(([^()]*)\)\s*[;{]")  # a prototype's
_UNNAMED = re.compile(r"^\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?:\*\s*)*$")  # f(size_t)
_CAST = re.compile(r"\(\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?:\*\s*)+\)")  # (D3DMATRIX *)
_BARE_CAST = re.compile(r"\(\s*([A-Za-z_]\w*)\s*\)\s*(?=[A-Za-z_&(]|\d)")
# a local's declaration in a body, at the start of a statement: two names in a row (`BOOL wait;`, `fd_set r = {0};`)
# are never an expression, nor is `T *p =` (a product isn't assigned to)
_LOCAL_DECL = re.compile(r"(?:^|(?<=[;{}]))\s*(?:(?:const|volatile|static|register)\s+)*([A-Za-z_]\w*)\b"
                         r"(?:\s*(\*[\s*]*)(?:\bconst\b\s*)?([A-Za-z_]\w*)\s*(?==)|\s+([A-Za-z_]\w*)\b\s*(?=[=;\[,]))",
                         re.M)
_FUNCTION_MACRO = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)\(([^)]*)\)[ \t]*(.*)$", re.M)
_TYPE_ARG = re.compile(r"\s*(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*([A-Za-z_]\w*)\s*([*\s]*)")
EXPANSIONS = 8


def declared_types(text: str) -> set[str]:
    """The names `text` (the header as read, with its typedefs) has for types: tags and typedef names."""
    typedefs = re.findall(r"\btypedef\b[^;{]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*;", text)
    typedefs += re.findall(r"\btypedef\b[^;{]*\(\s*\*\s*([A-Za-z_]\w*)\s*\)", text)
    typedefs += re.findall(r"\}\s*([A-Za-z_]\w*)\s*;", text)  # typedef struct {...} T;
    return {name for _, name in _TAG.findall(text)} | set(typedefs)


def _outside_braces(text: str) -> str:
    out, depth = [], 0
    for ch in text:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        out.append(ch if depth == 0 or ch == "{" and depth == 1 else " " if ch != "\n" else ch)
    return "".join(out)  # rounds of macro expansion: a macro naming a macro, a few deep


def expand_macros(header: str) -> str:
    """The header with its object-like macros (`#define MATCH_VOLATILE volatile`) put in where they're used, as a
    preprocessor would: pycparser has none, and a macro in a declaration (a member's qualifier, a list of methods)
    stopped it. Function-like ones stay as they are; the last definition of a name wins (both branches of a
    conditional are kept, seeds.c_only)."""
    macros = {name: value.strip() for name, value in _OBJECT_MACRO.findall(header)}
    if not macros:
        return header
    word = re.compile(r"\b(" + "|".join(map(re.escape, sorted(macros, key=len, reverse=True))) + r")\b")
    lines = []
    for line in header.splitlines():
        if not line.lstrip().startswith("#"):
            for _ in range(EXPANSIONS):
                expanded = word.sub(lambda m: macros[m.group(1)], line)
                if expanded == line:
                    break
                line = expanded
        lines.append(line)
    return "\n".join(lines)


def _call_args(text: str, at: int) -> tuple[list[str], int] | None:
    """The arguments of the call whose `(` is at `at` (split at its own commas), and where it ends; None if it
    doesn't close on this text."""
    depth, args, start = 0, [], at + 1
    for i in range(at, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return [*args, text[start:i]], i + 1
        elif ch == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
    return None


def expand_in_source(source: str, definitions: str, types: set[str]) -> str:
    """The function's own C with the macros of `definitions` (the header's and the source's own #defines) put in
    where pycparser can't read them as they are: an object-like one that stands for C's own words or nothing
    (`MATCH_VOLATILE bool found` is no declaration until it's `volatile`), and a function-like one called with a
    type (`F(int, 0x20) = v`, `MP_RAW(bool, 0x62C)`: no call takes a type). Others read as the names they are and
    stay (`#define CPlayerCar CPlayerCarRaceView` put in renamed the function's own class, so it wasn't found). A
    call of values stays a call, for the randomizer to move; the compiler gets the variant's expansion, which is
    what it would have made of the macro."""
    objects = {n: v.strip() for n, v in _OBJECT_MACRO.findall(definitions)
               if all(w in _C_WORDS for w in re.findall(r"\S+", v))}
    functions = {n: ([p.strip() for p in ps.split(",")], body.strip())
                 for n, ps, body in _FUNCTION_MACRO.findall(definitions) if "#" not in body}
    if not objects and not functions:
        return source
    known = types | C_TYPE_WORDS | set(CXX_TYPES)

    def is_type(arg: str) -> bool:
        m = _TYPE_ARG.fullmatch(arg)
        return bool(m) and (m.group(1) in known or "*" in m.group(2))

    word = re.compile(r"\b(" + "|".join(map(re.escape, sorted({*objects, *functions}, key=len, reverse=True))) + r")\b")

    def once(text: str) -> str:
        out, last = [], 0
        for m in word.finditer(text):
            if m.start() < last:
                continue
            name = m.group(1)
            if name in functions:
                at = re.match(r"\s*\(", text[m.end():])
                call = _call_args(text, m.end() + at.end() - 1) if at else None
                params, body = functions[name]
                if call is None or len(call[0]) != len(params) or not any(map(is_type, call[0])):
                    continue
                args = dict(zip(params, (a.strip() for a in call[0])))
                spelled = re.sub(r"\b(" + "|".join(map(re.escape, params)) + r")\b",
                                 lambda p, args=args: args[p.group(1)], body) if params != [""] else body
                out += [text[last:m.start()], spelled]
                last = call[1]
            elif name in objects:
                out += [text[last:m.start()], objects[name]]
                last = m.end()
        return "".join(out) + text[last:]

    for _ in range(EXPANSIONS):  # (a macro naming a macro, a few deep)
        expanded = once(source)
        if expanded == source:
            break
        source = expanded
    return source


def type_names(header: str, source: str) -> str:
    """Typedefs for the names C++ has as types and C doesn't: each struct, union and enum by its own name (unless
    something else is called that, a variable, a function or C's own typedef of it), and `bool`; and for the types
    the header names but never declares, which a system header the permuter isn't given does (`HANDLE`,
    `__m128`): to be read, a type only has to be one."""
    text = f"{header}\n{source}"
    untyped = re.sub(r"\btypedef\b[^;{]*;", "", text)  # (a typedef of it may come after its first use: still a type)

    def taken(name: str) -> bool:  # something other than a type is called this (C++'s `T(...)` and `sizeof(T)` are
        # the type: only the header's functions count)
        return bool(re.search(rf"(?<!struct )(?<!union )(?<!enum )(?<!class )\b{name}\s*[;=\[]", untyped)
                    or re.search(rf"\b{name}\s*\(", header))

    out = [f"typedef {c} {w};\n" for w, c in CXX_TYPES.items()
           if re.search(rf"\b{w}\b", text) and not re.search(rf"\btypedef\b[^;]*\b{w}\s*;", header)]
    named = set(CXX_TYPES)
    for kind, name in dict.fromkeys(_TAG.findall(text)):
        named.add(name)
        if not taken(name):
            out.append(f"typedef {kind.replace('class', 'struct')} {name} {name};\n")
    # where only a type can be: the header's declarations, the source's outside its functions' bodies (inside, `a * b`
    # can be a product), and casts
    declared = f"{header}\n{_outside_braces(source)}"
    unnamed = [m.group(1) for params in _PARAMETERS.findall(declared) for m in map(_UNNAMED.match, params.split(","))
               if m]
    # `(PDWORD) x`: a name only ever in parentheses before an operand is a type (one used otherwise may be a variable)
    bare = [n for n in _BARE_CAST.findall(text) if len(re.findall(rf"\b{n}\b", text)) == len(
        re.findall(rf"\(\s*{n}\s*\)", text))]
    # and a body's locals of a type the header doesn't have (the SDK's `WAVEFORMATEX format = {0};`, a type declared
    # where the header doesn't reach): 4% of an Xbox game's near misses stopped at one
    local = [m.group(1) for m in _LOCAL_DECL.finditer(source)]
    for name in dict.fromkeys([*_TYPE_SPOT.findall(declared), *unnamed, *_CAST.findall(text), *bare, *local]):
        if name not in _C_WORDS and name not in named and not taken(name):
            named.add(name)
            out.append(f"typedef int {name};\n")
    return "".join(out)


def main() -> int:
    sys.path.insert(0, sys.argv[1])  # decomp-permuter's folder: its `src` package and its own copy of pycparser
    from perm_pycparser import c_ast
    from src.ast_util import to_c
    from src.candidate import Candidate
    from src.helpers import get_default_randomization_weights
    from src.perm.perm import EvalState

    def variants(job: dict):
        name, count = job["function"], int(job["count"])
        # the source's own preprocessor lines (autopilot's drafts start with m2c's macros, seeds.M2C_MACROS) go too,
        # and back in front of each variant for the compiler: kept in, no draft ever parsed (#ifndef M2C_DEFINES)
        source = plain_c(job["source"])
        directives = "".join(line + "\n" for line in source.splitlines() if line.lstrip().startswith("#"))
        header = cxx_as_c(msvc_as_c(plain_c(expand_macros(plain_c(job["header"])), directives=False)))
        own = expand_in_source(plain_c(source, directives=False), f"{plain_c(job['header'])}\n{directives}",
                               declared_types(header))
        types = declared_types(f"{STAND_INS}{type_names(header, msvc_as_c(own))}{header}")
        msvc = Restorer(own, types)
        unit = (f"{STAND_INS}{type_names(header, msvc.source)}{header}\ntypedef int {SOURCE_STARTS};\n"
                f"{msvc.source}\n")
        name = msvc.name(name)
        rng = random.Random(job["seed"])
        try:
            weights = get_default_randomization_weights(job.get("family") or "base")
            first = Candidate.from_source(unit, EvalState(), name, weights, rng.randrange(1, MAX_SEED))
        except Exception as e:  # the C or the header isn't C pycparser reads (a macro call, a compiler extension)
            yield {"error": f"{type(e).__name__}: {e}"}
            return
        seam = next(node for node in first.ast.ext if isinstance(node, c_ast.Typedef) and node.name == SOURCE_STARTS)

        def own_part(ast) -> str:
            """The C after the seam: the function and its neighbours from the source, without the header. A header
            declaration the randomizer retyped is left out: the header isn't the permuter's to change, and a
            second, different declaration would only fail to compile."""
            return to_c(c_ast.FileAST(ast.ext[ast.ext.index(seam) + 1:]))

        seen = {own_part(first.ast)}  # the source as decomp-permuter prints it: not a variant
        cand = first
        for _ in range(count * TRIES_PER_VARIANT):
            if len(seen) > count:
                break
            try:
                if cand is None or rng.random() >= KEEP:
                    cand = Candidate.from_source(unit, EvalState(), name, weights, rng.randrange(1, MAX_SEED))
                cand.randomize_ast()
                variant = own_part(cand.ast)
            except Exception:  # a rewrite that trips over this code: start again from the source
                cand = None
                continue
            if variant not in seen:
                seen.add(variant)
                yield {"variant": directives + msvc.restore(variant)}

    for line in sys.stdin:
        if line.strip():
            for answer in variants(json.loads(line)):
                print(json.dumps(answer))
            print(json.dumps({"done": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
