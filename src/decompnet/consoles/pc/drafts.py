"""Windows PC: C that autopilot tries for a function, where m2c can't read the code (WindowsPC.exact_drafts and
compilable_draft). Each is compiled and judged like an agent's attempt, so a wrong one costs one compile, never a false
match.

- `exact`: the tiny shapes Visual C++ makes from a line or two of C, read straight from the machine code: a getter
  (`mov eax, [ecx+8]; ret`), a setter, a constant, a global object's constructor call (`push 0; mov ecx, <global>;
  call <ctor>; ret`), a thunk, a vtable store. A straight run of the instructions those use, with no branch, is
  written back as the C++ that makes it; anything else gets no exact draft. On a Visual C++ 6.0 game of 7,000
  functions, about one in ten has one of these shapes.
- `compilable`: Ghidra's draft made into C++ that compiles in the project's source file: Ghidra's types (ghidra.h),
  the function as the build names it, `this` for a method, the callees and globals it uses declared by their
  calling convention (from the decorated names the program's analysis gave them). Ghidra's drafts didn't compile as
  they were (undefined4, in_ECX, a method's `this` dropped), so autopilot couldn't try them at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import capstone
from capstone import x86 as cs_x86

from ...arch import x86

WORD = 4
ARG0 = 4  # [esp+4]: a function's first stack argument, above its return address
FRAME_ARG0 = 8  # [ebp+8]: the same, in a function that keeps an ebp frame (the caller's ebp is pushed below it)
_SYMBOL = re.compile(r"_*(func|D)_([0-9A-Fa-f]{8})")
_READER = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_READER.detail = True
REG, IMM, MEM = cs_x86.X86_OP_REG, cs_x86.X86_OP_IMM, cs_x86.X86_OP_MEM
_LOADS = {("mov", 4): "int", ("movzx", 1): "unsigned char", ("movsx", 1): "char", ("movzx", 2): "unsigned short",
          ("movsx", 2): "short", ("mov", 1): "char", ("mov", 2): "short", ("fld", 4): "float", ("fld", 8): "double"}
_STORES = {4: "int", 2: "short", 1: "char"}
_FLOATS = {4: "float", 8: "double"}
_FLOAT_OPS = {"fadd": "+", "fsub": "-", "fmul": "*", "fdiv": "/"}
_SUBREGISTERS = {"al": "eax", "ax": "eax", "cl": "ecx", "dl": "edx"}
SIZES = {"int": 4, "unsigned char": 1, "char": 1, "unsigned short": 2, "short": 2, "float": 4, "double": 8}
MAX_EXACT_BYTES = 64  # the shapes are a few instructions: a longer function isn't read


class _NoShape(Exception):
    """Code exact drafts don't write: a branch, a register they don't follow, a call they can't declare."""


def c_name(symbol: str) -> str | None:
    """func_XXXXXXXX / D_XXXXXXXX for a symbol however the program's analysis or Ghidra spells it (`_func_00401000@4`,
    `@func_00401000@8`, Ghidra's `__D_0066DDA4`, `_func_005D25A0_2147483647`); None for anything else (an import)."""
    m = _SYMBOL.match(symbol.lstrip("@"))
    return f"{m.group(1)}_{m.group(2).upper()}" if m else None


@dataclass(frozen=True)
class _V:
    """A value the code holds: a C expression and its type; `kind` "this" (ecx as it came in), "obj" (a global
    object's address, for a method call), "adj" (`this` moved by `text` bytes) or "" for any other."""
    text: str
    ctype: str = "int"
    kind: str = ""


THIS = _V("this", "", "this")


@dataclass
class _Lift:
    """What a straight run of instructions does, as C: its statements, the fields of `this` it uses, the callees."""
    addr: int
    known: str  # the header and file's text: names declared there aren't declared again
    regs: dict = field(default_factory=lambda: {"ecx": THIS})
    pending: list = field(default_factory=list)  # pushed for the next call, last pushed first
    pushed: int = 0
    frame: bool = False  # `push ebp; mov ebp, esp`: built with /Oy-
    stmts: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)  # offset -> C type
    methods: dict = field(default_factory=dict)  # a callee method's declaration -> None (in order)
    decls: dict = field(default_factory=dict)  # name -> its block-scope declaration
    args: dict = field(default_factory=dict)  # stack argument slot -> C type, as read
    this: bool = False  # whether ecx is read as it came in: a method
    returns: tuple | None = None  # (C type, the value or None, the bytes it takes off the stack)
    used: set = field(default_factory=set)

    @property
    def cls(self) -> str:
        return f"C_{self.addr:08X}"

    def symbol(self, name: str | None) -> str:
        found = c_name(name or "")
        if found is None:
            raise _NoShape(f"not a function or global of the game: {name}")
        return found

    def field(self, off: int, ctype: str) -> str:
        if self.fields.setdefault(off, ctype) != ctype:
            raise _NoShape("a field read as two types")
        self.this = True
        return f"unk{off:X}"

    def arg(self, slot: int, ctype: str) -> str:
        if self.args.setdefault(slot, ctype) != ctype:
            raise _NoShape("an argument read as two types")
        return f"a{slot}"

    def declare_global(self, name: str, ctype: str = "int") -> None:
        if not re.search(rf"\b{name}\b", self.known) and \
                self.decls.setdefault(name, f"extern {ctype} {name};") != f"extern {ctype} {name};":
            raise _NoShape(f"{name} used as two types")

    def declare_function(self, name: str, decl: str) -> None:
        if self.decls.setdefault(name, decl) != decl:
            raise _NoShape(f"{name} called two ways")

    def address(self, name: str) -> _V:
        """A function's or global's address, as the int it's pushed or stored as."""
        if name.startswith("D_"):
            self.declare_global(name)
            return _V(f"(int)&{name}")
        self.declare_function(name, self.decls.get(name, f"int {name}(...);"))
        return _V(f"(int){name}")

    def text(self, value: _V) -> str:
        if value.kind == "this":
            self.this = True
            return "this"
        if value.kind:
            raise _NoShape("an object's address used as a value")
        return value.text

    def object(self, value: _V | None) -> str | None:
        """What a method is called on, when ecx was set for the call: a global object, or `this` moved."""
        if value is None:
            raise _NoShape("ecx unknown at a call")
        if value.kind == "obj":
            self.declare_global(value.text)
            return f"(({self.cls}*)&{value.text})->"
        if value.kind == "adj":
            self.this = True
            return f"(({self.cls}*)((char*)this + {value.text}))->"
        return None


def _symbol_of(ins, op, relocs: dict[int, str], addr: int) -> str | None:
    """The symbol a relocation in this operand names (its address or its immediate), if any."""
    if op.type not in (IMM, MEM):
        return None
    return relocs.get(ins.address - addr + (ins.imm_offset if op.type == IMM else ins.disp_offset))


def _place(lift: _Lift, ins, op, sym: str | None) -> tuple:
    """Where a memory operand is: ("arg", slot), ("global", name) or ("field", offset)."""
    mem = op.mem
    if mem.index:
        raise _NoShape("an indexed operand")
    base = ins.reg_name(mem.base) if mem.base else None
    if base == "esp" or base == "ebp" and lift.frame:
        slot, rest = divmod(mem.disp - (FRAME_ARG0 if base == "ebp" else ARG0 + WORD * lift.pushed), WORD)
        if rest or slot < 0:
            raise _NoShape("a local variable")
        return ("arg", slot)
    if base is None and sym:
        return ("global", lift.symbol(sym))
    if base and lift.regs.get(base) == THIS:
        return ("field", mem.disp)
    raise _NoShape("an operand through a pointer exact drafts don't follow")


def _load(lift: _Lift, ins, op, sym: str | None, how: str = "mov") -> _V:
    """The value an operand reads, `how` (mov, movzx, movsx, fld) telling a memory operand's type."""
    if op.type == IMM:
        return lift.address(lift.symbol(sym)) if sym else \
            _V(str(op.imm if op.imm < 0x80000000 else op.imm - (1 << 32)), "char" if op.size == 1 else "int")
    if op.type == REG:
        name = ins.reg_name(op.reg)
        value = lift.regs.get(_SUBREGISTERS.get(name, name))
        if value is None:
            raise _NoShape(f"{name} read before it's set")
        lift.used.add(_SUBREGISTERS.get(name, name))
        return value
    ctype = _LOADS.get((how, op.size))
    if ctype is None:
        raise _NoShape("a load of another size")
    kind, at = _place(lift, ins, op, sym)
    if kind == "arg":
        return _V(lift.arg(at, ctype), ctype)
    if kind == "global":
        lift.declare_global(at, ctype)
        return _V(at, ctype)
    return _V(lift.field(at, ctype), ctype)


def _store(lift: _Lift, ins, op, sym: str | None, value: _V, ctype: str) -> None:
    kind, at = _place(lift, ins, op, sym)
    if kind == "arg":
        raise _NoShape("a store to an argument")
    if kind == "global":
        lift.declare_global(at, ctype)
    else:
        at = lift.field(at, ctype)
    lift.stmts.append(f"{at} = {lift.text(value)};")


def _set(lift: _Lift, reg: str, value: _V) -> None:
    reg = _SUBREGISTERS.get(reg, reg)
    if reg not in ("eax", "ecx", "edx"):
        raise _NoShape(f"{reg} set")
    lift.regs[reg] = value
    lift.used.discard(reg)


def _call(lift: _Lift, ins: list, k: int, m: str, callee: str) -> int:
    """A call or tail call: its statement, the callee's declaration (__cdecl when the caller cleans its arguments
    off the stack after, else __stdcall, or a method when ecx was set for it). Returns where the code goes on."""
    args = [lift.text(v) for v in reversed(lift.pending)]
    ecx = lift.regs.get("ecx")
    on = lift.object(ecx) if ecx != THIS else None
    cleaned = 0
    while m == "call" and k < len(ins) and cleaned < len(args) and ins[k].mnemonic == "pop" and ins[k].op_str == "ecx":
        cleaned, k = cleaned + 1, k + 1
    if m == "call" and not cleaned and k < len(ins) and ins[k].mnemonic == "add" and ins[k].op_str.startswith("esp, "):
        cleaned, k = ins[k].operands[1].imm // WORD, k + 1
    if m == "jmp" and args:
        raise _NoShape("a tail call with arguments")
    if cleaned and cleaned != len(args):
        raise _NoShape("a call whose arguments aren't all cleaned up at once")
    if on is not None:
        if cleaned:
            raise _NoShape("a method that doesn't clean its arguments")
        lift.methods.setdefault(f"void {callee}({', '.join(['int'] * len(args))});", None)
    elif args and not cleaned:  # the callee took its arguments off the stack: __stdcall
        lift.declare_function(callee, f"int __stdcall {callee}({', '.join(['int'] * len(args))});")
    else:
        lift.declare_function(callee, f"int {callee}(...);")
    lift.stmts.append(f"{on or ''}{callee}({', '.join(args)});")
    lift.pending, lift.pushed = [], lift.pushed - len(args)
    lift.regs = {}  # eax, ecx and edx belong to the callee now
    return k


def _lift(lift: _Lift, ins: list, relocs: dict[int, str]) -> int:
    """Walk the instructions, writing what they do into `lift`, up to its return or tail call (returns how many
    instructions that took); raises _NoShape at anything else."""
    k = 0
    if len(ins) > 1 and (ins[0].mnemonic, ins[0].op_str, ins[1].mnemonic, ins[1].op_str) == \
            ("push", "ebp", "mov", "ebp, esp"):
        lift.frame, k = True, 2
    while k < len(ins):
        i, k = ins[k], k + 1
        m, ops = i.mnemonic, i.operands
        syms = [_symbol_of(i, op, relocs, lift.addr) for op in ops]
        reg = i.reg_name(ops[0].reg) if ops and ops[0].type == REG else None
        if lift.frame and (m, i.op_str) == ("pop", "ebp"):
            if k >= len(ins) or ins[k].mnemonic not in ("ret", "jmp"):
                raise _NoShape("a frame closed before the end")
            lift.frame = False
        elif m in ("mov", "movzx", "movsx") and reg:
            value = _load(lift, i, ops[1], syms[1], m)
            if reg == "ecx" and value.text.startswith("(int)&D_"):
                value = _V(value.text.removeprefix("(int)&"), "", "obj")
            _set(lift, reg, value)
        elif m == "mov" and ops[0].type == MEM:
            if ops[1].type == IMM and syms[0] and syms[1]:
                raise _NoShape("an address stored to a global")
            ctype = _STORES.get(ops[0].size)
            if ctype is None:
                raise _NoShape("a store of another size")
            _store(lift, i, ops[0], syms[0], _load(lift, i, ops[1], syms[1]), ctype)
        elif m == "xor" and reg and ops[1].type == REG and ops[0].reg == ops[1].reg:
            _set(lift, reg, _V("0", "char" if ops[0].size == 1 else "int"))
        elif m == "or" and reg and ops[1].type == IMM and ops[1].imm in (-1, 0xFFFFFFFF):
            _set(lift, reg, _V("-1"))
        elif m == "lea" and reg and not ops[1].mem.index and ops[1].mem.base and \
                lift.regs.get(i.reg_name(ops[1].mem.base)) == THIS:
            _set(lift, reg, _V(f"(int)&{lift.field(ops[1].mem.disp, 'int')}"))
        elif m == "add" and reg == "ecx" and ops[1].type == IMM and lift.regs.get("ecx") == THIS:
            # (`sub ecx, N; jmp` is an adjustor thunk, which Visual C++ makes for a class with two bases: C moving
            # `this` back comes out as `add ecx, -N`)
            lift.this = True
            lift.regs["ecx"] = _V(str(ops[1].imm), "", "adj")
        elif m == "push":
            if ops[0].type == MEM and _place(lift, i, ops[0], syms[0])[0] != "arg":
                raise _NoShape("a push of memory exact drafts don't follow")
            lift.pending.append(_load(lift, i, ops[0], syms[0]))
            lift.pushed += 1
        elif m in ("call", "jmp") and ops[0].type == IMM:
            if lift.frame:
                raise _NoShape("a call inside a frame")
            k = _call(lift, ins, k, m, lift.symbol(syms[0]))
            if m == "jmp":
                lift.returns = ("void", None, 0)
                return k
        elif m == "fld" and ops[0].type == MEM:
            lift.regs["st0"] = _load(lift, i, ops[0], syms[0], "fld")
        elif m in ("fldz", "fld1"):
            lift.regs["st0"] = _V("0.0f" if m == "fldz" else "1.0f", "float")
        elif m in _FLOAT_OPS and len(ops) == 1 and ops[0].type == MEM and "st0" in lift.regs:
            value, st = _load(lift, i, ops[0], syms[0], "fld"), lift.regs["st0"]
            ctype = "double" if "double" in (value.ctype, st.ctype) else "float"
            lift.regs["st0"] = _V(f"{st.text} {_FLOAT_OPS[m]} {value.text}", ctype)
        elif m in ("fstp", "fst") and ops[0].type == MEM and ops[0].size in _FLOATS and "st0" in lift.regs:
            _store(lift, i, ops[0], syms[0], lift.regs["st0"], _FLOATS[ops[0].size])
            if m == "fstp":
                del lift.regs["st0"]
        elif m in ("ret", "retn"):
            if lift.pending or lift.frame:
                raise _NoShape("pushed and never called, or a frame left open")
            pops = ops[0].imm if ops else 0
            eax = lift.regs.get("eax")
            if "st0" in lift.regs:
                lift.returns = (lift.regs["st0"].ctype, lift.regs["st0"].text, pops)
            elif eax is not None and "eax" not in lift.used:
                lift.this |= eax == THIS
                lift.returns = (f"{lift.cls}*" if eax == THIS else eax.ctype, lift.text(eax), pops)
            else:
                lift.returns = ("void", None, pops)
            return k
        else:
            raise _NoShape(f"{m} {i.op_str}")
    raise _NoShape("it doesn't end in a return or a tail call")


def _parameters(lift: _Lift, pops: int) -> list[str] | None:
    """The function's parameters, each stack slot an int unless read as another type (a double takes two); None
    when the slots read don't fit in what it takes off the stack."""
    slots = pops // WORD if pops else max(lift.args, default=-1) + 1
    out, slot = [], 0
    while slot < slots:
        ctype = lift.args.get(slot, "int")
        out.append(f"{ctype} a{slot}")
        slot += SIZES[ctype] // WORD if ctype == "double" else 1
    return out if slot == slots else None


def exact(code: bytes, addr: int, relocs: list, known: str = "") -> list[tuple[str, str]]:
    """The C++ that makes a function of one of the tiny shapes Visual C++ compiles a line or two of C into, as
    [(prelude, definition)] for autopilot: the prelude (a struct for `this` and the methods called) goes after the
    file's includes. [] for any other code. `relocs`: the function's relocations as functions.json keeps them
    ([offset, type, symbol, addend]); `known`: the header and file's text, whose declarations are used as they are.
    A function that keeps an ebp frame matches only where its file is built with /Oy- (file_flags)."""
    if len(code) > MAX_EXACT_BYTES:
        return []
    ins = list(_READER.disasm(code, addr))
    while ins and ins[-1].mnemonic in x86._PADDING:
        ins.pop()
    if not ins:
        return []
    lift = _Lift(addr, known)
    try:
        if _lift(lift, ins, {r[0]: r[2] for r in relocs}) < len(ins):
            return []  # more code after its end (a function the analysis ran on into): not all of it is read
    except _NoShape:
        return []
    ret, value, pops = lift.returns
    params = _parameters(lift, pops)
    if params is None:
        return []
    name, sig = f"func_{addr:08X}", ", ".join(params) or "void"
    members, at = [], 0
    for off, ctype in sorted(lift.fields.items()):
        if off < at:
            return []  # fields that overlap
        if off > at:
            members.append(f"char pad{at:X}[0x{off - at:X}];")
        members.append(f"{ctype} unk{off:X};")
        at = off + SIZES[ctype]
    if lift.this:
        members.append(f"{ret} {name}({sig});")
    members += list(lift.methods)
    prelude = (f"struct {lift.cls} {{\n" + "".join(f"    {m}\n" for m in members) + "};\n") if members else ""
    # a method takes its arguments off the stack itself (__thiscall); a free function that does is __stdcall
    head = f"{ret} {lift.cls}::{name}({sig})" if lift.this else f"{ret} {'__stdcall ' if pops else ''}{name}({sig})"
    body = [*lift.decls.values(), *lift.stmts] + ([f"return {value};"] if value is not None else [])
    return [(prelude, head + " {\n" + "".join(f"    {s}\n" for s in body) + "}\n")]



# ---------------------------------------------------------------------------------------------- Ghidra's drafts

GHIDRA_HEADER = "ghidra.h"
GHIDRA_H = """/* Ghidra's names for the types and operations it can't tell yet, so its machine drafts compile as they are:
 * autopilot tries them (`#include "ghidra.h"`). The game's own types are better: agents write those in common.h. */
#ifndef GHIDRA_H
#define GHIDRA_H

typedef unsigned char undefined;
typedef unsigned char undefined1;
typedef unsigned short undefined2;
typedef unsigned int undefined3;
typedef unsigned int undefined4;
typedef unsigned __int64 undefined8;
typedef unsigned char byte;
typedef unsigned char uchar;
typedef unsigned short ushort;
typedef unsigned short word;
typedef unsigned int uint;
typedef unsigned int uint3;
typedef unsigned long ulong;
typedef unsigned int dword;
typedef __int64 longlong;
typedef unsigned __int64 ulonglong;
typedef unsigned __int64 qword;
typedef double longdouble;  /* (Visual C++'s long double is a double) */
typedef double float10;
typedef int code(...);

#define CONCAT11(a, b) ((unsigned short)(((unsigned short)(unsigned char)(a) << 8) | (unsigned char)(b)))
#define CONCAT22(a, b) ((unsigned int)(((unsigned int)(unsigned short)(a) << 16) | (unsigned short)(b)))
#define CONCAT31(a, b) ((unsigned int)(((unsigned int)(a) << 8) | (unsigned char)(b)))
#define CONCAT13(a, b) ((unsigned int)(((unsigned int)(unsigned char)(a) << 24) | ((unsigned int)(b) & 0xffffff)))
#define CONCAT44(a, b) ((unsigned __int64)(((unsigned __int64)(unsigned int)(a) << 32) | (unsigned int)(b)))
#define SUB41(x, n) ((unsigned char)((unsigned int)(x) >> ((n) * 8)))
#define SUB42(x, n) ((unsigned short)((unsigned int)(x) >> ((n) * 8)))
#define SUB84(x, n) ((unsigned int)((unsigned __int64)(x) >> ((n) * 8)))
#define ZEXT14(x) ((unsigned int)(unsigned char)(x))
#define ZEXT24(x) ((unsigned int)(unsigned short)(x))
#define ZEXT48(x) ((unsigned __int64)(unsigned int)(x))
#define SEXT14(x) ((int)(signed char)(x))
#define SEXT24(x) ((int)(short)(x))
#define SEXT48(x) ((__int64)(int)(x))

#endif
"""
# What no C stands for: registers Ghidra saw read before they're set (other than ecx: `this`), its stack and
# segment views, imports through their pointers, partial variables (`local_4._1_3_`), the flag helpers
_UNWRITABLE = re.compile(r"\b(unaff_\w+|in_stack_\w+|extraout_\w+|in_FS_OFFSET|in_GS_OFFSET|in_E[ABD]X|in_E[SD]I|"
                         r"in_EBP|in_ESP|stack0x\w+|_+imp_\w+|halt_baddata|swi|LOCK|UNLOCK|CARRY\d|SCARRY\d|SBORROW\d|"
                         r"POPCOUNT|_alloca_probe)\b|\.\_\d+_\d+_")
_GHIDRA_SYMBOL = re.compile(r"\b_*(func|D)_([0-9A-Fa-f]{8})(?:_\d+)?\b")
_HEAD = re.compile(r"^(?P<ret>[^\n;{}]*?)\s*\b(?P<conv>__cdecl|__stdcall|__fastcall|__thiscall)?\s*"
                   r"(?P<name>\w*func_(?P<addr>[0-9A-Fa-f]{8})\w*)\s*\((?P<params>[^()]*)\)\s*\{", re.M)
_LOCAL = re.compile(r"^[ \t]+(?P<type>[\w ]+?)\s*(?P<ptr>\*+)?\s*(?P<name>\w+)(?:\s*\[\w+\])?;[ \t]*$", re.M)


def _class_of(known: str, name: str) -> str | None:
    """The struct or class the header (or file) declares `name` a method of, or None."""
    for m in re.finditer(rf"\b{name}\s*\(", known):
        depth, at = 0, m.start()
        while at > 0:
            at -= 1
            if known[at] == "}":
                depth += 1
            elif known[at] == "{":
                if depth == 0:
                    found = re.search(r"\b(?:struct|class)\s+(\w+)[^;{}()]*$", known[:at])
                    if found:
                        return found.group(1)
                    break
                depth -= 1
    return None


def _calls(body: str, name: str):
    """(start of the argument list, its end, the arguments) of each call of `name` in body."""
    for m in re.finditer(rf"\b{name}\s*\(", body):
        depth, at, args, start = 1, m.end(), [], m.end()
        while at < len(body) and depth:
            c = body[at]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            if (c == "," and depth == 1) or depth == 0:
                args.append(body[start:at].strip())
                start = at + 1
            at += 1
        yield m.end(), at - 1, [a for a in args if a]


def compilable(draft: str, symbol: str, symbols: dict[str, str], known: str = "") -> tuple[str, str] | None:
    """Ghidra's draft of a function as C++ that compiles in its source file, as (prelude, definition), or None when
    the draft holds what no C stands for (_UNWRITABLE). `symbol`: the function's decorated name (_func_00401000@8:
    __stdcall); `symbols`: func_<address> -> each function's decorated name, for the callees' conventions; `known`: the
    header and file's text, whose declarations (and a class that declares the function) are used as they are.

    Ghidra drafts each function of a delinked object without knowing what's outside it: a method reads `in_ECX`,
    callees are their symbols with no prototype, a global is `_D_<address>` when its address is taken (a byte, to
    Ghidra) and `__D_<address>` when it's read or written (a word)."""
    text = re.sub(r"/\*.*?\*/", "", draft, flags=re.S)
    head = _HEAD.search(text)
    if head is None or _UNWRITABLE.search(text) or c_name(symbol) is None:
        return None
    name = c_name(symbol)
    body = text[head.end():].rstrip()
    if not body.endswith("}"):
        return None
    body = body[:-1]
    params = [p.strip() for p in head.group("params").split(",") if p.strip() not in ("", "void")]
    method = re.search(r"\bin_ECX\b", body) is not None or head.group("conv") == "__thiscall"
    prelude = ""
    lines = []
    if method:
        if head.group("conv") == "__thiscall" and params:
            this = params.pop(0)
        else:
            local = re.search(r"^[ \t]*(?P<decl>[\w *]+?\bin_ECX);[ \t]*\n", body, re.M)
            if local is None:
                return None
            this = local.group("decl")
            body = body[:local.start()] + body[local.end():]
        ctype = this.rsplit(None, 1)[0] + "*" * this.split()[-1].count("*")
        lines.append(f"{ctype} {this.split()[-1].lstrip('*')} = ({ctype})this;")
    elif re.match(r"@", symbol) and head.group("conv") != "__fastcall":
        return None  # named __fastcall, drafted as another: its register arguments are lost
    pops = int(symbol.rsplit("@", 1)[1]) if re.search(r"[^@]@\d+$", symbol) else 0
    if pops and pops != WORD * len(params) and pops < 1 << 16:
        return None  # Ghidra read another number of arguments than the function takes off the stack
    sig = ", ".join(params) or "void"
    ret = head.group("ret").strip() or "void"
    cls = _class_of(known, name) if method else None
    if method and cls is None:
        cls = f"C_{int(head.group('addr'), 16):08X}"
        prelude = f"struct {cls} {{\n    {ret} {name}({sig});\n}};\n"
    conv = "" if method else "__fastcall " if symbol.startswith("@") else "__stdcall " if pops else ""
    signature = f"{ret} {cls}::{name}({sig})" if method else f"{ret} {conv}{name}({sig})"
    locals_ = {m.group("name"): (m.group("type"), m.group("ptr") or "") for m in _LOCAL.finditer(body)}
    params_ = {p.split()[-1].lstrip("*"): (p.rsplit(None, 1)[0], "*" * p.split()[-1].count("*")) for p in params}
    types = {**params_, **locals_}

    declared = {}
    for m in _GHIDRA_SYMBOL.finditer(body):
        other = f"{m.group(1)}_{m.group(2).upper()}"
        if other in declared or other == name or re.search(rf"\b{other}\b", known):
            declared.setdefault(other, None)
            continue
        if other.startswith("D_"):
            wide = "__" + other in body or re.search(rf"\b__+{other}\b", body)
            declared[other] = f"extern {'undefined4' if wide else 'undefined'} {other};"
            continue
        callee = symbols.get(other, f"_{other}")
        word = int(callee.rsplit("@", 1)[1]) // WORD if re.search(r"[^@]@\d+$", callee) else None
        if callee.startswith("@"):
            declared[other] = f"int __fastcall {other}({', '.join(['int'] * (word or 0)) or 'void'});"
        elif word is not None and word < 1 << 14:
            declared[other] = f"int __stdcall {other}({', '.join(['int'] * word) or 'void'});"
        else:
            declared[other] = f"int {other}(...);"
    # each symbol as the build names it: a global Ghidra saw as a byte at its address keeps that view
    body = re.sub(r"&_D_([0-9A-Fa-f]{8})(?:_\d+)?\b", lambda m: f"((undefined *)&D_{m.group(1).upper()})", body)
    body = _GHIDRA_SYMBOL.sub(lambda m: f"{m.group(1)}_{m.group(2).upper()}", body)
    for other, decl in declared.items():
        if decl is None or "(" not in decl or decl.endswith("(...);"):
            continue
        wanted = decl.count("int") - 1 if "(void)" not in decl else 0
        for start, end, args in reversed(list(_calls(body, other))):
            if len(args) != wanted:
                return None  # Ghidra passes another number of arguments than the callee takes
            body = body[:start] + ", ".join(f"(int)({a})" for a in args) + body[end:]
    # a call's result kept in a pointer, or returned as one, is cast to it (the callees return int)
    for var, (vtype, ptr) in types.items():
        if ptr:
            body = re.sub(rf"\b{var} = (?=(?:D|func)_[0-9A-F]{{8}}\()", f"{var} = ({vtype} {ptr})", body)
    if "*" in ret:
        body = re.sub(r"\breturn (?=func_[0-9A-F]{8}\()", f"return ({ret})", body)
    decls = [d for d in declared.values() if d] + lines
    code = (f"#include \"{GHIDRA_HEADER}\"\n{signature}\n{{\n" + "".join(f"  {d}\n" for d in decls)
            + body.lstrip("\n").rstrip() + "\n}\n")
    return prelude, code
