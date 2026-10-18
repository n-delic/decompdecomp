"""Machine-generated starting points: run m2c on a function's target code to give the first agent
a C draft instead of bare assembly.

m2c (GPL-3.0) is installed as a separate pinned tool (Compilers & tools in the app, or `decomp setup`) and run as a
program, never imported. It reads GNU-as syntax, which we rebuild from the target object: capstone
disassembly with relocations shown as %hi/%lo/symbols and branch targets turned into labels.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import scoring, toolchains
from .elfobj import FunctionObj

# What m2c's --valid-syntax output needs to compile: its unknown types and macros, defined the simplest way that
# keeps the code's meaning (written for decompdecomp; the names are m2c's interface).
M2C_MACROS = """\
#ifndef M2C_DEFINES
#define M2C_DEFINES
typedef s32 M2C_UNK;
typedef s8 M2C_UNK8;
typedef s16 M2C_UNK16;
typedef s32 M2C_UNK32;
#define M2C_FIELD(expr, type_ptr, offset) (*(type_ptr)((s8 *)(expr) + (offset)))
#define M2C_BITWISE(type, expr) ((type)(expr))
#define M2C_ERROR(desc) (0)
#define M2C_CARRY 0
#define M2C_OVERFLOW(a) (0)
#endif
"""
_BRANCH = re.compile(r"^(b|beq|bne|beqz|bnez|bgtz|blez|bltz|bgez|bltzal|bgezal|beql|bnel|beqzl|bnezl|bgtzl|blezl|"
                     r"bltzl|bgezl|bc1t|bc1f|bc1tl|bc1fl|j)$")


def m2c_target(toolchain_id: str) -> str | None:
    """m2c's target for C that `toolchain_id` built ("mips-ido-c"): the console that lists the compiler says m2c's
    dialect (consoles/). None when m2c can't read its code."""
    from . import consoles  # (here, not at the top: consoles reach the engine, which reaches this module)

    found = consoles.compiler(toolchain_id)
    target = found[0].m2c_target(toolchain_id) if found else ""
    return f"{target}-c" if target else None


def available() -> bool:
    return toolchains.installed("m2c") is not None


def gnu_asm(fn: FunctionObj) -> str:
    """The function as a GNU as source file m2c understands."""
    insns = scoring.disassemble(fn)
    targets: set[int] = set()
    body = []
    for ins in insns:
        ops = ins.operands
        parts = [p.strip() for p in ops.split(",")] if ops else []
        if _BRANCH.match(ins.mnemonic) and parts and re.fullmatch(r"0x[0-9a-f]+|\d+", parts[-1]):
            target = int(parts[-1], 0)
            targets.add(target)
            parts[-1] = f".L{fn.name}_{target:X}"
            ops = ", ".join(parts)
        body.append((ins.offset, f"{ins.mnemonic} {ops}".rstrip()))
    lines = [".set noat", ".set noreorder", ".set gp=64", "", f"glabel {fn.name}"]
    for off, text in body:
        if off in targets:
            lines.append(f".L{fn.name}_{off:X}:")
        lines.append(f"/* {off:06X} */ {text}")
    return "\n".join(lines) + "\n"


_CONDITIONAL = re.compile(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)")
_CPLUSPLUS = re.compile(r"^\s*(?:defined\s*\(?\s*)?__cplusplus\b\s*\)?\s*$")


def c_only(header: str) -> str:
    """The header as a C compiler sees it, as far as m2c needs: what's only for C++ (`#ifdef __cplusplus` blocks,
    which drafts are C, and `#if 0` blocks) taken out. Other conditionals keep both their branches, as before. GTA San
    Andreas's header keeps its classes in an `#ifdef __cplusplus` block: kept, every draft's context failed to parse
    and m2c ran a second time without any of it (October 2026: none of its drafts had the header)."""
    out = []
    open_: list[list[bool]] = []  # per open conditional: [this branch is left out, the #else branch is]
    for line in header.splitlines():
        m = _CONDITIONAL.match(line)
        if m:
            kind, cond = m.group(1), m.group(2).strip()
            cxx = bool(_CPLUSPLUS.match(cond))
            if kind in ("if", "ifdef"):
                left_out = cxx or (kind == "if" and cond == "0")
                open_.append([left_out, False])
            elif kind == "ifndef":
                open_.append([False, cxx])  # `#ifndef __cplusplus`: its #else is the C++ branch
            elif kind in ("else", "elif") and open_:
                open_[-1][0] = open_[-1][1]
            elif kind == "endif" and open_:
                open_.pop()
            continue
        if not any(left_out for left_out, _ in open_):
            out.append(line)
    return "\n".join(out) + ("\n" if header.endswith("\n") else "")


def _context_for_m2c(header: str) -> str:
    # m2c parses the context with pycparser and does not run a preprocessor.
    return "\n".join(line for line in c_only(header).splitlines() if not line.lstrip().startswith("#"))


# ---------------------------------------------------------------- the part of a header one function uses

# Words of C (and the C++ a header may hold) that never name a declaration of the header
_KEYWORDS = frozenset("""auto break case char const continue default do double else enum extern float for goto if
inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile
while _Bool __attribute__ __inline__ __restrict__ __extension__ asm __asm__ class public private protected virtual
operator template typename namespace using friend mutable explicit bool true false new delete this""".split())
_TAGS = frozenset(("struct", "union", "enum", "class"))
_TOKEN = re.compile(r"(?P<comment>/\*.*?\*/|//[^\n]*)|(?P<pp>^[ \t]*#(?:\\\n|[^\n])*)"
                    r"|(?P<string>\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')|(?P<ident>[A-Za-z_]\w*)"
                    r"|(?P<punct>[{}()\[\];,:*=])", re.S | re.M)
_DEFINE = re.compile(r"\s*#\s*define\s+([A-Za-z_]\w*)")
_LINE_TAIL = re.compile(r"[ \t]*(?:/\*(?:(?!\*/)[^\n])*\*/|//[^\n]*)[ \t]*(?=\n|$)")


@dataclass(frozen=True)
class Declaration:
    """One top-level declaration of a header: its text (with the comment before it), the names it declares, and the
    other names it mentions (the types, macros and declarations it needs)."""
    text: str
    names: frozenset[str]
    uses: frozenset[str]


def _declares(tokens: list[tuple[str, str, int]]) -> set[str]:
    """The names one declaration declares, from its tokens (kind, text, depth): a struct, union, enum or class tag
    with a body, the constants of an enum, and its declarators (typedef names, variables, functions)."""
    names: set[str] = set()
    top = [(kind, text) for kind, text, depth in tokens if depth == 0]
    for i, (kind, text) in enumerate(top):  # tags that are defined here (a body or a base class follows)
        if text in _TAGS and i + 1 < len(top) and top[i + 1][0] == "ident":
            after = top[i + 2][1] if i + 2 < len(top) else ";"
            if after in ("{", ":") or (after == ";" and i == 0):
                names.add(top[i + 1][1])
    if "enum" in [text for _, text, _ in tokens[:3]]:  # its constants: the first word of each item of its body
        item = False
        for kind, text, depth in tokens:
            if item and depth == 1 and kind == "ident":
                names.add(text)
            item = (depth == 0 and text == "{") or (depth == 1 and text == ",")
    # declarators: after the body if there is one, split at the commas outside any brackets
    body_end = max((i for i, (_, text, depth) in enumerate(tokens) if text == "}" and depth == 0), default=-1)
    parts: list[list[tuple[str, str, int]]] = [[]]
    for kind, text, depth in tokens[body_end + 1:]:
        if depth == 0 and text == ",":
            parts.append([])
        else:
            parts[-1].append((kind, text, depth))
    for part in parts:
        pointer = next((j for j in range(len(part) - 2) if part[j][1] == "(" and part[j][2] == 0
                        and part[j + 1][1] == "*"), None)  # (*name)(...): a pointer to a function
        if pointer is not None:
            name = next((text for kind, text, _ in part[pointer + 2:] if kind == "ident"), None)
        else:
            idents = [text for kind, text, depth in part if kind == "ident" and depth == 0 and text not in _KEYWORDS]
            name = idents[-1] if idents else None
        if name:
            names.add(name)
    opener = next((i for i, (_, text, depth) in enumerate(tokens) if text == "{" and depth == 0), None)
    if opener and tokens[opener - 1][1] == ")" and body_end == len(tokens) - 1:  # a function defined in the header
        before = tokens[:next(i for i, (_, text, depth) in enumerate(tokens) if text == "(" and depth == 0)]
        names.update(text for kind, text, depth in before[-1:] if kind == "ident" and text not in _KEYWORDS)
    return names


def declarations(header: str) -> list[Declaration]:
    """The header's top-level declarations in order: structs, unions, enums and classes, typedefs, prototypes,
    variables and macros. A light reader, not a C parser: what it can't place still keeps its text and the names it
    mentions, so nothing a function needs goes missing, at worst a little more comes with it."""
    out: list[Declaration] = []
    tokens: list[tuple[str, str, int]] = []
    start, depth, function_body = 0, 0, False

    def finish(end: int) -> None:
        nonlocal start, tokens, function_body
        tail = _LINE_TAIL.match(header, end)  # a comment after it on its line is its own (`}; /* 0x48 */`)
        end = tail.end() if tail else end
        text = header[start:end].strip()
        if text and any(kind != "comment" for kind, _, _ in tokens):
            names = _declares([t for t in tokens if t[0] != "comment"])
            uses = {text for kind, text, _ in tokens if kind == "ident"} - names - _KEYWORDS
            out.append(Declaration(text, frozenset(names), frozenset(uses)))
        start, tokens, function_body = end, [], False

    for m in _TOKEN.finditer(header):
        kind, text = m.lastgroup, m.group()
        if kind == "pp":
            if depth == 0:
                finish(m.start())
                define = _DEFINE.match(text)
                if define:  # a macro: it declares its name and mentions what its value does
                    uses = set(re.findall(r"[A-Za-z_]\w*", text[define.end():])) - _KEYWORDS - {define.group(1)}
                    out.append(Declaration(text.strip(), frozenset({define.group(1)}), frozenset(uses)))
                start = m.end()
            continue
        if kind == "string":
            continue
        if text in "{([":
            if text == "{" and depth == 0:
                # a function defined in the header (static inline ...): a parameter list came before its body
                function_body = any(t == "(" and d == 0 for _, t, d in tokens) and "=" not in {t for _, t, d in tokens if d == 0}
            tokens.append((kind, text, depth))
            depth += 1
            continue
        if text in "})]":
            depth = max(0, depth - 1)
            tokens.append((kind, text, depth))
            if text == "}" and depth == 0 and function_body:
                finish(m.end())
            continue
        tokens.append((kind, text, depth))
        if text == ";" and depth == 0:
            finish(m.end())
    finish(len(header))
    return out


class HeaderIndex:
    """A header's declarations, with what declares each name: `used(roots)` is the part a function needs."""

    def __init__(self, header: str):
        self.decls = declarations(header)
        self.by_name: dict[str, list[int]] = {}
        for i, d in enumerate(self.decls):
            for name in d.names:
                self.by_name.setdefault(name, []).append(i)

    def used(self, roots) -> list[Declaration]:
        """The declarations of the names in `roots` and, in turn, of every name those mention, in the header's
        order (so each comes after what it needs, as in the header)."""
        return [self.decls[i] for i in sorted(self.reach(roots))]

    def reach(self, roots) -> list[int]:
        """Where those declarations are in `decls`, nearest first: those `roots` name, then what those need..."""
        taken: dict[int, None] = {}  # (ordered: the order they were reached in)
        queue = list(dict.fromkeys(roots))
        queued = set(queue)
        for name in queue:
            for i in self.by_name.get(name, ()):
                if i not in taken:
                    taken[i] = None
                    more = [n for n in self.decls[i].uses if n not in queued]
                    queued.update(more)
                    queue.extend(sorted(more))
        return list(taken)


_ASM_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_ASM_NAME = re.compile(r"(?<![\w$.%])[A-Za-z_]\w*")
_ASM_FIRST = re.compile(r"^\s*([\w.]+)", re.M)  # a line's mnemonic, or its directive (glabel, .set)


def asm_names(asm_text: str) -> set[str]:
    """The symbols an assembly listing mentions, which is where m2c reads names from: every word of it but the
    mnemonics and directives, registers ($a0), local labels (.L...) and relocation operators (%hi)."""
    text = _ASM_COMMENT.sub(" ", asm_text)
    return set(_ASM_NAME.findall(text)) - set(_ASM_FIRST.findall(text))


def generate(fn: FunctionObj, toolchain_id: str | None, context_header: str = "", timeout: float = 20) -> str | None:
    """C draft for one function, or None when m2c or the target is unavailable or m2c fails."""
    return generate_from_asm(gnu_asm(fn), fn.name, toolchain_id, context_header, timeout=timeout)


# ---------------------------------------------------------------- a draft of a C++ method

VPTR_BYTES = 4  # the vtable pointer an object of a class with virtual functions starts with
# m2c's line that starts the function: `void *func_00123456(void *arg0, s32 arg1) {` ({name}: the function's)
_SIGNATURE = r"^(?P<ret>[^\n]*?)\b{name}\((?P<params>[^()\n]*)\) \{{$"
_POINTER_ARG0 = re.compile(r"^.*\*\s*arg0$")
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


def method_draft(draft: str, name: str, cls: str, fields: list[tuple[int, int, str]], vptr: int, role: str,
                 more: bool = False) -> str | None:
    """m2c's draft of a function the class recovery knows as a method of `cls` (`role`: "virtual function 3"),
    shown as C++: `Class::name(...)` with m2c's arg0 as `this`, after a declaration of the class with the fields
    (offset, size, type) it uses (`more`: it uses more than those), named as m2c names fields (unk4): `this->unk4`
    in the draft is one of them.
    None when that can't be done safely: a first argument that isn't a pointer, the vtable pointer not first, fields
    that overlap, a class name that isn't a C++ name. The draft is then shown as m2c wrote it."""
    found = re.search(_SIGNATURE.format(name=re.escape(name)), draft, re.M)
    if not found or vptr != 0 or not _IDENTIFIER.match(cls):
        return None
    params = [p.strip() for p in found.group("params").split(",")]
    if not _POINTER_ARG0.match(params[0]):
        return None
    rest = ", ".join(params[1:])
    body = ["public:", "    /* +0x00: the vtable pointer (the compiler adds it to a class with virtual functions) */"]
    at = VPTR_BYTES
    for offset, size, ctype in sorted(fields):
        if offset < at:
            return None  # fields that overlap: no one layout says them both
        if offset > at:
            body.append(f"    u8 pad{at:X}[0x{offset - at:X}];")
        field = f"unk{offset:X}"
        if ctype.endswith("]"):  # an array: u8[3]
            base, _, count = ctype.partition("[")
            decl = f"{base} {field}[{count}"
        else:
            decl = f"{ctype}{'' if ctype.endswith('*') else ' '}{field}"
        body.append(f"    {decl}; /* +0x{offset:02X} */")
        at = offset + size
    if more:
        body.append("    /* ... and more fields it uses: get_class */")
    ret = found.group("ret")
    body.append(f"    {ret}{name}({rest});")
    note = (f"/* C++: {name} is {role} of class {cls}, found from the vtables constructors store (get_class has\n"
            "   its whole layout). m2c's arg0 is `this`; the class's fields here are the ones this function uses. */")
    head, tail = draft[:found.start()], draft[found.start():]
    tail = re.sub(r"\barg0\b", "this", tail[tail.index("{"):])
    return f"{head.rstrip()}\n{note}\nclass {cls} {{\n" + "\n".join(body) + f"\n}};\n\n{ret}{cls}::{name}({rest}) {tail}"


class DraftError(Exception):
    """m2c couldn't make the draft asked for (strict runs only), with its own reason."""


def generate_from_asm(asm_text: str, name: str, toolchain_id: str | None, context_header: str = "",
                      data_asm: str | None = None, timeout: float = 20, valid: bool = False,
                      strict: bool = False) -> str | None:
    """C draft from GNU/splat-style assembly text (plus optional data, for jump tables and strings). `valid`:
    m2c's compilable form (unknown types and expressions as M2C_ macros, see M2C_MACROS), for trying drafts as is.
    `strict`: a context m2c can't read raises DraftError instead of drafting without it."""
    target = m2c_target(toolchain_id or "")
    if not target:
        return None
    return run_m2c(asm_text, name, target, context_header, data_asm, timeout, valid, strict)


MAX_REASON_CHARS = 600  # of m2c's reason for a failure, passed on to an agent


def _failure(res: subprocess.CompletedProcess) -> str:
    """m2c's reason for a failed run: its "Decompilation failure" note, else the end of what it printed."""
    out = res.stdout or ""
    note = out[out.find("Decompilation failure"):] if "Decompilation failure" in out else (res.stderr or out)
    return note.strip().strip("*/").strip()[-MAX_REASON_CHARS:] or f"m2c stopped with code {res.returncode}"


def run_m2c(asm_text: str, name: str, target: str, context_header: str = "", data_asm: str | None = None,
            timeout: float = 20, valid: bool = False, strict: bool = False) -> str | None:
    """Run m2c for one function with an explicit target triple (e.g. ppc-mwcc-c++)."""
    if not available():
        return None
    if target.startswith("gba"):  # a public GBA decomp (pret style): function macros, pre-UAL load mnemonics
        asm_text = re.sub(r"^\s*(?:non_word_aligned_)?thumb_func_start\s+(\w+)", r"glabel \1", asm_text, flags=re.M)
        asm_text = re.sub(r"^\s*arm_func_start\s+(\w+)", r"glabel \1", asm_text, flags=re.M)
        asm_text = re.sub(r"\bld(s[hb])\b", r"ldr\1", asm_text)
    m2c = toolchains.venv_bin(toolchains.path("m2c") / "venv", "m2c")
    with tempfile.TemporaryDirectory(prefix="decompnet-m2c-") as tmp:
        asm = Path(tmp) / f"{name}.s"
        asm.write_text(asm_text, encoding="utf-8")
        argv = [str(m2c), *(["--valid-syntax"] if valid else []), "-t", target, "-f", name, str(asm)]
        if data_asm:
            (Path(tmp) / "data.s").write_text(data_asm, encoding="utf-8")
            argv.append(str(Path(tmp) / "data.s"))
        if context_header.strip():
            ctx = Path(tmp) / "context.c"
            ctx.write_text(_context_for_m2c(context_header), encoding="utf-8")
            argv[1:1] = ["--context", str(ctx)]
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                 encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            if strict:
                raise DraftError(f"m2c took longer than {timeout:g} s") from None
            return None
        if res.returncode != 0 and strict:
            raise DraftError(_failure(res))
        if res.returncode != 0 and "--context" in argv:  # an unparsable context must not cost us the seed
            i = argv.index("--context")
            res = subprocess.run(argv[:i] + argv[i + 2:], capture_output=True, text=True, timeout=timeout,
                                 encoding="utf-8", errors="replace")
        out = res.stdout.strip()
        return out if res.returncode == 0 and out else None
