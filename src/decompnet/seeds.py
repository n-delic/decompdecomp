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

from decompnet.hostenv import NO_WINDOW

from . import compile_timing, limits, m2c_fp64, m2c_jump_tables, m2c_types, scoring, toolchains
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


def with_m2c_macros(source: str) -> str:
    """Add the compatibility preamble when a draft needs its names."""
    return M2C_MACROS + '\n' + source if re.search(r'\bM2C_\w+', source) else source


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


def c_only(header: str, cxx: bool = False) -> str:
    """The header as a C compiler sees it, as far as m2c needs: what's only for C++ (`#ifdef __cplusplus` blocks,
    which drafts are C, and `#if 0` blocks) taken out. Other conditionals keep both their branches, as before. A PC
    game's header kept its classes in an `#ifdef __cplusplus` block: kept, every draft's context failed to parse and
    m2c ran a second time without any of it (October 2026: none of its drafts had the header).
    `cxx`: as a C++ compiler sees it instead, for a C++ function (decomp-permuter reads its classes' data)."""
    out = []
    open_: list[list[bool]] = []  # per open conditional: [this branch is left out, the #else branch is]
    for line in header.splitlines():
        m = _CONDITIONAL.match(line)
        if m:
            kind, cond = m.group(1), m.group(2).strip()
            is_cxx = bool(_CPLUSPLUS.match(cond))
            if kind in ("if", "ifdef"):
                if is_cxx:
                    open_.append([not cxx, cxx])
                else:
                    open_.append([kind == "if" and cond == "0", False])
            elif kind == "ifndef":
                open_.append([is_cxx and cxx, is_cxx and not cxx])  # `#ifndef __cplusplus`: #else is C++'s
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


def intrinsic_leaf(asm_text: str, name: str, toolchain_id: str | None) -> str | None:
    """A fixed CP0/FPU leaf (decomprepair's mips_count, mips_status, ...) when the toolchain carries their header:
    its `intrinsics` is mips_debug_break. The Status write with return spacing is the KMC GCC's alone."""
    from decomprepair import mips_compare, mips_count, mips_fcsr, mips_status, mips_status_return_spacing

    from . import toolchains

    try:
        tc = toolchains.get(toolchain_id or "")
    except toolchains.ToolchainError:
        return None
    registered = tc.intrinsics == "decompnet.arch.mips_debug_break"
    kmc = registered and tc.id == "gcc-2.7.2-kmc"
    for module, allowed in ((mips_status_return_spacing, kmc), (mips_compare, registered), (mips_fcsr, registered),
                            (mips_status, registered), (mips_count, registered)):
        if source := module.candidate(asm_text, name, allowed):
            return source
    return None


def generate_from_asm(asm_text: str, name: str, toolchain_id: str | None, context_header: str = "",
                      data_asm: str | None = None, timeout: float = 20, valid: bool = False,
                      strict: bool = False, options: tuple[str, ...] = (), *, resolve=None) -> str | None:
    """The draft of `_generate_from_asm`; a compilable one also declares what m2c used without declaring (a table
    read, the frame's slots), last, so the repairs above that read a bare `sp` or a `jtbl_` still see it."""
    source = _generate_from_asm(asm_text, name, toolchain_id, context_header, data_asm, timeout, valid, strict, options,
                                resolve=resolve)
    if source and valid:
        from decomprepair import m2c_undeclared_drafts
        source = m2c_undeclared_drafts.rewrite(source, name, asm_text, context_header) or source
    return source


def _generate_from_asm(asm_text: str, name: str, toolchain_id: str | None, context_header: str = "",
                      data_asm: str | None = None, timeout: float = 20, valid: bool = False,
                      strict: bool = False, options: tuple[str, ...] = (), *, resolve=None) -> str | None:
    """C draft from GNU/splat-style assembly text (plus optional data, for jump tables and strings). `valid`:
    m2c's compilable form (unknown types and expressions as M2C_ macros, see M2C_MACROS), for trying drafts as is.
    `strict`: a context m2c can't read raises DraftError instead of drafting without it.
    `resolve` looks up project addresses for repairs that need to keep a relocated object separate.
    """
    if source := intrinsic_leaf(asm_text, name, toolchain_id):
        return source
    target = m2c_target(toolchain_id or "")
    if not target:
        return None
    if not valid or toolchain_id != 'gcc-2.7.2-kmc':
        source = run_m2c(asm_text, name, target, context_header, data_asm, timeout, valid, strict, options)
        if not valid:
            return source
        if target.startswith('mips'):
            return _compilable(source, name, asm_text, context_header, toolchain_id, resolve=resolve)
        return _compilable(source)

    from decomprepair import mips_entry_loops

    if entry_loop := mips_entry_loops.candidate(asm_text, name, context_header):
        return entry_loop

    from decomprepair import (
        aligned_allocator_request,
        aligned_stack_read,
        byte_cursor_postincrements,
        direct_mapped_address,
        field_snapshot_copies,
        float_matrix_identity,
        global_pointer_local_repairs,
        guard_inversion_repairs,
        indexed_field_cursor,
        indirect_call_drafts,
        kmc_cast_repairs,
        large_stack_drafts,
        likely_branch_cursor,
        loop_storage_shapes,
        masked_slot_dispatch,
        measured_address_repairs,
        measured_callback_loop,
        measured_mmio_polling,
        measured_stack_drafts,
        measured_word_drafts,
        ordered_halfword_updates,
        packed_matrix_conversion,
        passthrough_argument_repairs,
        relocated_byte_table,
        relocated_global_update,
        shared_default_drafts,
        shared_return_repairs,
        short_leaf_drafts,
        split_dispatch_zero_case,
        stack_fill_drafts,
        stack_header_buffer,
        stack_read_retry,
        stack_word_index_drafts,
        switch_pointer_calls,
        unaligned_stack_drafts,
        uncached_read_repairs,
    )

    draft_asm = likely_branch_cursor.draft_asm(asm_text)
    source = _compilable(run_m2c(draft_asm, name, target, context_header, data_asm, timeout, valid, strict, options),
                         name, asm_text, context_header, toolchain_id, resolve=resolve)
    if not source:
        return None
    if 'M2C_UNALIGNED32' in source and (draft := unaligned_stack_drafts.rewrite(
            source, name, asm_text, context_header, resolve)):
        return draft
    if words := measured_word_drafts.variants(source, name, asm_text, context_header):
        return words[0][1]
    if leaves := short_leaf_drafts.variants(source, name, asm_text, context_header):
        return leaves[0][1]
    switch_asm = _mips_table_aliases(asm_text, data_asm) if data_asm and 'switch (' in source else asm_text
    return (measured_stack_drafts.rewrite(source, name, asm_text, context_header, resolve=resolve)
            or large_stack_drafts.rewrite(source, name, asm_text, context_header)
            or masked_slot_dispatch.rewrite(source, name, switch_asm, data_asm, context_header)
            or switch_pointer_calls.rewrite(source, name, switch_asm, data_asm, context_header)
            or shared_default_drafts.explicit_default_labels(source, name, switch_asm, data_asm)
            or shared_default_drafts.rewrite(source, name, switch_asm, data_asm, context_header)
            or uncached_read_repairs.rewrite(source, name, asm_text, resolve)
            or measured_callback_loop.rewrite(source, name, asm_text, context_header, resolve)
            or indirect_call_drafts.rewrite(source, name, asm_text, context_header, resolve)
            or measured_mmio_polling.rewrite(source, name, asm_text, context_header)
            or likely_branch_cursor.rewrite(source, name, asm_text, context_header)
            or relocated_byte_table.rewrite(source, name, asm_text, context_header, resolve)
            or relocated_global_update.rewrite(source, name, asm_text, context_header, resolve)
            or relocated_global_update.rewrite_reload_wrapper(source, name, asm_text, context_header, resolve)
            or relocated_global_update.rewrite_branch_byte(source, name, asm_text, context_header, resolve)
            or relocated_global_update.rewrite_countdown(source, name, asm_text, context_header, resolve)
            or stack_fill_drafts.rewrite(source, name, asm_text, context_header)
            or stack_fill_drafts.rewrite_ascending(source, name, asm_text, context_header)
            or stack_fill_drafts.rewrite_word_clear(source, name, asm_text, context_header)
            or stack_word_index_drafts.rewrite(source, name, asm_text, context_header)
            or stack_header_buffer.rewrite(source, name, asm_text, context_header)
            or stack_read_retry.rewrite(source, name, asm_text, context_header)
            or stack_read_retry.rewrite_inline(source, name, asm_text, context_header)
            or float_matrix_identity.rewrite(source, name, asm_text, context_header)
            or packed_matrix_conversion.rewrite(source, name, asm_text, context_header)
            or byte_cursor_postincrements.rewrite(source, name, asm_text, context_header)
            or aligned_allocator_request.rewrite(source, name, asm_text, context_header)
            or indexed_field_cursor.rewrite(source, name, asm_text, context_header)
            or split_dispatch_zero_case.rewrite(source, name, asm_text, context_header)
            or field_snapshot_copies.rewrite(source, name, asm_text, context_header)
            or ordered_halfword_updates.rewrite(source, name, asm_text, context_header, resolve)
            or aligned_stack_read.rewrite(source, name, asm_text)
            or guard_inversion_repairs.rewrite_byte_toggle(source, name, asm_text, context_header)
            or kmc_cast_repairs.rewrite_float_table(source, name, asm_text, context_header)
            or direct_mapped_address.rewrite(source, name, asm_text, context_header)
            or global_pointer_local_repairs.rewrite_list_transfer(source, name, asm_text, context_header)
            or global_pointer_local_repairs.rewrite_linked_insert(source, name, asm_text, context_header)
            or shared_return_repairs.rewrite_global_guard(source, name, asm_text)
            or measured_address_repairs.rewrite_byte_offsets(source, name, asm_text, context_header)
            or passthrough_argument_repairs.missing_entry_argument(source, name, asm_text, context_header)
            or loop_storage_shapes.measured_copy_setup(source, name, asm_text, context_header)
            or source)


def _compilable(source: str | None, name='', asm='', header='', toolchain=None, *, resolve=None) -> str | None:
    """Repair generated C before the first compiler trial, using the target when available."""
    if not source:
        return source
    from decomprepair import large_switch_drafts
    source = large_switch_drafts.rewrite(source) or source
    if (toolchain or '').startswith('ido'):
        # IDO refuses arithmetic on `void *` (GCC 2.x allows it): the names m2c adds to are byte pointers
        from decomprepair import void_pointer_arithmetic
        source = void_pointer_arithmetic.rewrite(source, name) or source
        source = void_pointer_arithmetic.externs(source, name) or source
    from decomprepair import void_pointer_arithmetic as stores
    source = stores.stores(source) or source  # a store m2c printed as a call through a cast, which no compiler reads
    if asm:
        from decomprepair import draft_call_recovery

        if repaired := draft_call_recovery.variants(source, name, asm, header, toolchain, resolve=resolve):
            return repaired[0][1]
        # what the recovery left: a call to a code address inside the same overlay slot (`jal .L8033193C_raw_slot_09`)
        # is named for its label, and `.L...` is no C name: it is a function like the others (`func_<address>`), as the
        # split names the piece
        source = re.sub(r"(?<![\w.])\.L([0-9A-Fa-f]{8}(?:_[a-z][a-z0-9]*(?:_[A-Za-z0-9]+)*)?)(?![\w.])", r"func_\1", source)
    return source


def drop_saved_register_reads(source: str) -> str:
    """m2c writes `var_s0 = saved_reg_s0;` where the code reads a callee-saved register before it sets it (a value it
    only conditionally replaces). `saved_reg_s0` is declared nowhere, so no draft with one compiles; the local simply
    stays unset, which is what the original register did."""
    return re.sub(r"^[ \t]*\w+ = saved_reg_\w+;[ \t]*\n", "", source, flags=re.M)


_UNSET_DOUBLE = re.compile(r"GLUE_F64\(M2C_ERROR\(/\* Read from unset register \$(f\w+) \*/\), 0U\)")
_UNSET_REGISTER = re.compile(r"M2C_ERROR\(/\* Read from unset register \$(\w+) \*/\)")
_UNSET_READ = re.compile(r"\b(?:unk)?sp[0-9A-Fa-f]+\b|\bsaved_reg_\w+\b|\bunset_\w+\b|\bsubroutine_arg\d+\b|(?<![\w.])sp\b")
_NOT_A_TYPE = {"return", "else", "case", "goto", "do", "sizeof"}


def declare_unset_reads(source: str, name: str) -> str:
    """m2c reads a stack slot or a callee-saved register that nothing in the function set (`sp24` passed on as a
    callee's seventh argument, `saved_reg_s0 != 0`) and names it without declaring it: 36 of one game's 406
    functions without a draft failed with an undeclared sp24. The original read whatever the caller left there, so the
    draft declares it as an unset local: it compiles, and the search decides what the slot really holds."""
    head = re.search(rf"^[^\n;]*\b{re.escape(name)}\s*\([^)]*\)\s*\{{[ \t]*$", source, flags=re.M)
    if not head:
        return source
    body = source[head.end():]
    missing = []
    for found in dict.fromkeys(_UNSET_READ.findall(body)):
        declared = re.compile(rf"^[ \t]*(?:\w+[ \t]+)+(?:\(\s*\*+|\**){re.escape(found)}\b[^(=\n]*[;=,\[)]", re.M)
        if not any(m[0].split()[0] not in _NOT_A_TYPE for m in declared.finditer(body)):
            missing.append(found)
    if not missing:
        return source
    def declaration(found):
        if found == "sp":  # the frame's own address (`var_t1 = sp;`): a buffer
            return "    u8 sp[256];\n"
        # a slot m2c indexes (`&sp10[0]`) is a buffer, not a word
        if found.startswith("unset_f"):  # a floating-point register nothing set
            return f"    f32 {found};\n"
        return f"    s32 {found}[16];\n" if re.search(rf"\b{found}\[", body) else f"    s32 {found};\n"
    lines = "".join(declaration(found) for found in missing)
    return source[:head.end()] + "\n" + lines.rstrip("\n") + source[head.end():]


MAX_REASON_CHARS = 600  # of m2c's reason for a failure, passed on to an agent


def _failure(res: subprocess.CompletedProcess) -> str:
    """m2c's reason for a failed run: its "Decompilation failure" note, else the end of what it printed."""
    out = res.stdout or ""
    note = out[out.find("Decompilation failure"):] if "Decompilation failure" in out else (res.stderr or out)
    reason = note.strip().strip("*/").strip()[-MAX_REASON_CHARS:] or f"m2c stopped with code {res.returncode}"
    if "jump table is not provided" in reason:
        # (Penny Racers: 32 functions whose overlay data was never imported; nothing here can invent the case targets)
        reason += (" The imported game data has no bytes at the table's address, so no draft is possible until they are"
                   " added to the project (memory_regions.json for an overlay); the switch can be worked out by hand.")
    return reason


def _execute_m2c(argv, timeout):
    from . import m2c_sessions
    try:
        result=m2c_sessions.run(argv,timeout)
    except (OSError,ValueError):
        result=None  # A missing/incompatible worker can use the ordinary pinned CLI.
    return result if result is not None else subprocess.run(limits.capped(argv),capture_output=True,text=True,
        timeout=timeout,encoding="utf-8",errors="replace",creationflags=NO_WINDOW)


_MIPS_TABLE_LABEL = re.compile(r"^[ \t]*dlabel[ \t]+((jtbl_|D_)([0-9A-Fa-f]{8}))[ \t]*$", re.M)
_MIPS_DATA_RELOC = re.compile(r"(%(?:hi|lo)\()D_([0-9A-Fa-f]{8})(\))")


def _mips_table_aliases(asm: str, data: str) -> str:
    """Use the supplied table's name when the draft's relocation uses its address alias."""
    aliases = {}
    for match in _MIPS_TABLE_LABEL.finditer(data):
        name, prefix, address = match[1], match[2], match[3].lower()
        # A table with two spellings for the same address needs its original relocation.
        aliases[address] = name if prefix == "jtbl_" and aliases.get(address, name) == name else None
    if not aliases:
        return asm

    def replace(match):
        table = aliases.get(match[2].lower())
        return match[1] + table + match[3] if table else match[0]

    return _MIPS_DATA_RELOC.sub(replace, asm)


_DELAY_SLOT_LABEL = re.compile(r"Label \._m2c_(\.?L[0-9A-Fa-f]{8}\w*?)before refers to a delay slot")


_ENTRY_BRANCH = re.compile(r"Cannot find branch target (\w+)")
_BRANCH_LINE = re.compile(r"^[ \t]*(?:/\*.*?\*/[ \t]*)?(?:b[a-z]*|j)[ \t]")


def entry_label(asm_text: str, name: str) -> str:
    """A loop that branches back to the function's own entry (a string compare's `bnez $a2, func_8006AA00`) names the
    function itself as a branch target, which m2c can't find. A local label after the entry (and a `nop`, so the loop
    keeps the registers it changed rather than starting from the arguments again), and the branches pointed at
    it, say the same thing."""
    label = f".L{name}_entry"
    lines, out, placed = asm_text.split("\n"), [], False
    for line in lines:
        if _BRANCH_LINE.match(line):
            line = re.sub(rf"\b{re.escape(name)}\b", label, line)
        out.append(line)
        if not placed and re.match(rf"^[ \t]*glabel[ \t]+{re.escape(name)}[ \t]*$", line):
            out.append("nop")  # the loop's state is then a join of the entry and the branch, not the arguments again
            out.append(f"{label}:")
            placed = True
    return "\n".join(out) if placed else asm_text


_FLOAT_SAVE = re.compile(r"^[ \t]*(?:/\*.*?\*/[ \t]*)?s(?:wc1|dc1)[ \t]+\$(?:fs\d+f?|f(?:2\d|3[01]))[ \t]*,[ \t]*[-+\w]+\(\$sp\)[ \t]*$",
                         re.M)


def drop_float_saves(asm_text: str) -> str:
    """The prologue's stores of callee-saved FPU registers (`swc1 $fs0, 0x18($sp)`) removed. A function that keeps a
    float in $f20..$f31 saves the caller's value first, and m2c reads that register as a use of a register nothing
    set ("Read from unset register $f20"), so no draft of it compiled (a fifth of one game's drafts that failed to
    compile). The save is the compiler's, not the C's, so the draft is the same without it."""
    return _FLOAT_SAVE.sub("", asm_text)


@compile_timing.timed('generation')
def run_m2c(asm_text: str, name: str, target: str, context_header: str = "", data_asm: str | None = None,
            timeout: float = 20, valid: bool = False, strict: bool = False, options: tuple[str, ...] = ()) -> str | None:
    """Run m2c for one function with an explicit target triple (e.g. ppc-mwcc-c++).

    m2c refuses a label that a branch-likely handling puts on a delay slot ("refers to a delay slot: add a nop").
    A `nop` before that label is what it asks for and changes nothing the draft shows, so it is added and m2c runs
    again (a function can have several such labels)."""
    for _ in range(8):
        try:
            return _run_m2c(asm_text, name, target, context_header, data_asm, timeout, valid, strict, options)
        except DraftError as error:
            if (entry := _ENTRY_BRANCH.search(str(error))) and entry[1] == name:
                looped = entry_label(asm_text, name)
                if looped == asm_text:
                    raise
                asm_text = looped
                continue
            if "Read from unset register $f" in str(error) and (saved := drop_float_saves(asm_text)) != asm_text:
                asm_text = saved
                continue
            found = _DELAY_SLOT_LABEL.search(str(error))
            if not found:
                raise
            label = re.escape(found[1])
            padded = re.sub(rf"^([ \t]*{label}:[ \t]*)$", r"nop\n\1", asm_text, count=1, flags=re.M)
            if padded == asm_text:
                raise
            asm_text = padded
    return _run_m2c(asm_text, name, target, context_header, data_asm, timeout, valid, strict, options)


def _run_m2c(asm_text: str, name: str, target: str, context_header: str = "", data_asm: str | None = None,
             timeout: float = 20, valid: bool = False, strict: bool = False, options: tuple[str, ...] = ()) -> str | None:
    if any(option not in ('--no-switches', '--no-stack-spill', '--gotos-only', '--stack-structs') for option in options):
        raise ValueError('Unsupported machine-draft option.')
    if not available():
        return None
    original = asm_text
    if target.startswith('mips-'):
        from .arch.mips import abi_register_names
        from .rom.split import draft_asm

        asm_text = m2c_fp64.pair_free(abi_register_names(asm_text))
        if data_asm:
            asm_text = m2c_jump_tables.inline_tables(asm_text, data_asm)
        if data_asm:
            asm_text = draft_asm(_mips_table_aliases(asm_text, data_asm), name, data_asm=data_asm)
    if target.startswith("gba"):  # a public GBA decomp (pret style): function macros, pre-UAL load mnemonics
        asm_text = re.sub(r"^\s*(?:non_word_aligned_)?thumb_func_start\s+(\w+)", r"glabel \1", asm_text, flags=re.M)
        asm_text = re.sub(r"^\s*arm_func_start\s+(\w+)", r"glabel \1", asm_text, flags=re.M)
        asm_text = re.sub(r"\bld(s[hb])\b", r"ldr\1", asm_text)
    m2c = toolchains.venv_bin(toolchains.path("m2c") / "venv", "m2c")
    with tempfile.TemporaryDirectory(prefix="decompnet-m2c-") as tmp:
        asm = Path(tmp) / f"{name}.s"
        asm.write_text(asm_text, encoding="utf-8")
        argv = [str(m2c), *options, *(["--valid-syntax"] if valid else []), "-t", target, "-f", name, str(asm)]
        if data_asm:
            (Path(tmp) / "data.s").write_text(data_asm, encoding="utf-8")
            argv.append(str(Path(tmp) / "data.s"))
        if context_header.strip():
            ctx = Path(tmp) / "context.c"
            ctx.write_text(_context_for_m2c(context_header), encoding="utf-8")
            argv[1:1] = ["--context", str(ctx)]
        try:
            res = _execute_m2c(argv,timeout)
        except subprocess.TimeoutExpired:
            if strict:
                raise DraftError(f"m2c took longer than {timeout:g} s") from None
            return None
        if res.returncode != 0 and strict:
            raise DraftError(_failure(res))
        if res.returncode != 0 and "--context" in argv:  # an unparsable context must not cost us the seed
            i = argv.index("--context")
            res = _execute_m2c(argv[:i] + argv[i + 2:],timeout)
        out = res.stdout.strip()
        # m2c can exit successfully after emitting a partial function and bare diagnostics.
        # Its error placeholders discard instructions; they are explanations, never baseline C.
        errors = [line.strip() for line in out.splitlines()
                  if re.match(r"\s*(?://\s*Error:|Bad function call operand\b)", line)
                  or (valid and re.search(r"\bM2C_ERROR\s*\(", line))]
        if errors and valid and ("Read from unset register $f" not in out or drop_float_saves(original) == original):
            # (a prologue's float saves come out of the assembly first, in run_m2c: they are not reads)
            # a register the code reads before anything set it (an argument past the ones m2c saw, a temporary the caller
            # left): the original read whatever was there, so the draft reads an unset local
            replaced = _UNSET_DOUBLE.sub(lambda found: "unset_" + found[1], out)  # (a double whose half nothing set)
            replaced = _UNSET_REGISTER.sub(lambda found: "unset_" + found[1], replaced)
            if replaced != out:
                out = replaced
                errors = [line.strip() for line in out.splitlines()
                          if re.match(r"\s*(?://\s*Error:|Bad function call operand\b)", line)
                          or re.search(r"\bM2C_ERROR\s*\(", line)]
        reason = '\n'.join(errors)[-MAX_REASON_CHARS:]
        if res.returncode != 0 or not out or errors:
            if strict:
                raise DraftError(reason or (_failure(res) if out or res.stderr or res.returncode else 'm2c produced no C for this function.'))
            return None
        if valid and "--no-switches" not in options and "/* irregular */" in out:
            # a switch m2c could not structure leaves `case` labels outside any `switch`; as gotos it compiles
            return _run_m2c(original, name, target, context_header, data_asm, timeout, valid, strict,
                            (*options, "--no-switches"))
        return m2c_types.repair(declare_unset_reads(drop_saved_register_reads(out), name)) if valid else out
