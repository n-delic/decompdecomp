"""The browser build's IR (port/wasm/web.py): what bemem.py does to wasm2c's C for the native build, done to each
function's IR instead, because in the browser the WebAssembly runs as it is.

- Every load and store goes through the game's memory helpers (web/be.c: `port_ld32`, `port_st32`...): big-endian,
  as on the N64, and an uncached address (0xA0000000 up) through the host. The varargs a variadic function reads
  stay as the compiler wrote them (little-endian: the backend fills the buffer and `va_start` the `va_list`, where
  the native build swapped those too).
- A call through a pointer goes through `port_icall` with its arguments in a buffer of 64-bit slots, and every
  function gets a "universal" entry (`F.port_u`) that takes them from there: a function pointer in the game is the
  function's console address, and the N64 never checked a callee's type where wasm traps on a mismatch. A callee
  declaring more arguments than the call passes gets the rest from the register words (as adapt.py's calls leave
  them, and as MIPS did); a void callee called for a value gives its $v0 word.
- Each file registers its functions (console address, function, universal entry) at start-up, for port_icall.
"""
from __future__ import annotations

import re

from adapt import REGS, balanced, parse_arg, split_top

SLOTS = 16  # arguments a call through a pointer passes in slots (the result has its own)
INT = re.compile(r"i(1|8|16|32|64)$")
NAME = r"%[\w.$\"-]+"
HELPERS = """
declare zeroext i8 @port_ld8(ptr)
declare zeroext i16 @port_ld16(ptr)
declare i32 @port_ld32(ptr)
declare i64 @port_ld64(ptr)
declare float @port_ldf(ptr)
declare double @port_ldd(ptr)
declare void @port_st8(ptr, i8 zeroext)
declare void @port_st16(ptr, i16 zeroext)
declare void @port_st32(ptr, i32)
declare void @port_st64(ptr, i64)
declare void @port_stf(ptr, float)
declare void @port_std(ptr, double)
declare void @port_icall(ptr, ptr, i32)
declare void @port_register(ptr, i32)
declare i16 @llvm.bswap.i16(i16)
declare i32 @llvm.bswap.i32(i32)
declare i64 @llvm.bswap.i64(i64)
"""
LOADS = {"i8": "port_ld8", "i16": "port_ld16", "i32": "port_ld32", "i64": "port_ld64", "float": "port_ldf",
         "double": "port_ldd", "ptr": "port_ld32"}
STORES = {"i8": "port_st8", "i16": "port_st16", "i32": "port_st32", "i64": "port_st64", "float": "port_stf",
          "double": "port_std", "ptr": "port_st32"}


class Fresh:
    def __init__(self):
        self.n = 0

    def __call__(self, what="t"):
        self.n += 1
        return f"%port_{what}{self.n}"


def _va_slots(body: list[str]) -> set[str]:
    """The slots `va_start` and `va_copy` write (a pointer, as the backend writes it: little-endian). Everything
    else about varargs is big-endian: each variadic call passes its extra arguments byte-swapped (_swap_varargs),
    so the buffer the backend fills holds them as the N64 would, for va_arg and for the game's own `char *`
    walking of it (a printf core that takes its arguments as `char *args`)."""
    slots = set()
    for line in body:
        if (m := re.search(r"call void @llvm\.va_(?:start|copy)(?:\.p0)?\(ptr (" + NAME + r")(?:, ptr (" + NAME + r"))?",
                           line)):
            slots.update(g for g in m.groups() if g)
    return slots


def _swapped(out, fresh, indent, t, v):
    """`v` with its bytes swapped, as the same type; None for a type that isn't a number."""
    if t in ("i32", "i64", "i16"):
        r = fresh("b")
        out.append(f"{indent}{r} = call {t} @llvm.bswap.{t}({t} {v})")
        return r
    if t == "ptr":
        a, b, r = fresh("b"), fresh("b"), fresh("b")
        out += [f"{indent}{a} = ptrtoint ptr {v} to i32", f"{indent}{b} = call i32 @llvm.bswap.i32(i32 {a})",
                f"{indent}{r} = inttoptr i32 {b} to ptr"]
        return r
    if t in ("float", "double"):
        it = "i32" if t == "float" else "i64"
        a, b, r = fresh("b"), fresh("b"), fresh("b")
        out += [f"{indent}{a} = bitcast {t} {v} to {it}", f"{indent}{b} = call {it} @llvm.bswap.{it}({it} {a})",
                f"{indent}{r} = bitcast {it} {b} to {t}"]
        return r
    return None


def _swap_varargs(line, fresh, out) -> bool:
    """A call with a variadic function type, its arguments past the fixed ones byte-swapped (_va_slots)."""
    m = re.search(r"\bcall (?:\w+ )*?(\S+) \(([^()]*\.\.\.)\)\s*([@%][\w.$\"-]+|inttoptr \([^)]*\))\(", line)
    if not m:
        return False
    fixed = len([p for p in split_top(m.group(2)) if p != "..."])
    open_paren = m.end() - 1
    close = balanced(line, open_paren)
    args = split_top(line[open_paren + 1:close])
    if len(args) <= fixed:
        return False
    indent = re.match(r"\s*", line).group(0)
    new = list(args[:fixed])
    for a in args[fixed:]:
        t, v = parse_arg(a)
        w = _swapped(out, fresh, indent, t, v)
        if w is None:
            return False
        new.append(f"{t} {w}")
    out.append(line[:open_paren + 1] + ", ".join(new) + line[close:])
    return True


def _value_to_i64(out, fresh, t, v, unsigned):
    if t == "i64":
        return v
    if t == "double":
        r = fresh("d")
        out.append(f"  {r} = bitcast double {v} to i64")
        return r
    if t == "float":
        a, b = fresh("f"), fresh("f")
        out += [f"  {a} = bitcast float {v} to i32", f"  {b} = zext i32 {a} to i64"]
        return b
    if t == "ptr":
        a, b = fresh("p"), fresh("p")
        out += [f"  {a} = ptrtoint ptr {v} to i32", f"  {b} = zext i32 {a} to i64"]
        return b
    if INT.match(t):
        r = fresh("x")
        out.append(f"  {r} = {'zext' if unsigned or t == 'i1' else 'sext'} {t} {v} to i64")
        return r
    return None


def _i64_to(out, fresh, v, t, name=None):
    """`v` (an i64) as type t, in `name` when given (else a fresh value); the value's name."""
    r = name or fresh("c")
    if t == "i64":
        out.append(f"  {r} = add i64 {v}, 0")
    elif t == "double":
        out.append(f"  {r} = bitcast i64 {v} to double")
    elif t in ("float", "ptr"):
        a = fresh("c")
        out.append(f"  {a} = trunc i64 {v} to i32")
        out.append(f"  {r} = {'bitcast' if t == 'float' else 'inttoptr'} i32 {a} to {t}")
    elif INT.match(t):
        out.append(f"  {r} = trunc i64 {v} to {t}")
    else:
        return None
    return r


def _memory(line, native, fresh, out) -> bool:
    """A load or store rewritten as a helper call; False when the line is neither (or one left as it is)."""
    if (m := re.match(r"^(\s*)(" + NAME + r") = load (volatile )?(.*)$", line)):
        indent, name, _, rest = m.groups()
        parts = split_top(rest)
        if len(parts) < 2 or not parts[1].startswith("ptr "):
            return False
        t, ptr = parts[0].strip(), parts[1][4:].strip()
        if t not in LOADS or ptr in native:
            return False
        if t == "ptr":
            w = fresh("w")
            out += [f"{indent}{w} = call i32 @port_ld32(ptr {ptr})", f"{indent}{name} = inttoptr i32 {w} to ptr"]
        else:
            out.append(f"{indent}{name} = call {t} @{LOADS[t]}(ptr {ptr})")
        return True
    if (m := re.match(r"^(\s*)store (volatile )?(.*)$", line)):
        indent, _, rest = m.groups()
        parts = split_top(rest)
        if len(parts) < 2 or not parts[1].startswith("ptr "):
            return False
        tv = parts[0].strip()
        t = tv.split()[0]
        v = tv[len(t):].strip()
        ptr = parts[1][4:].strip()
        if t not in STORES or ptr in native:
            return False
        if t == "ptr":
            w = fresh("w")
            out += [f"{indent}{w} = ptrtoint ptr {v} to i32", f"{indent}call void @port_st32(ptr {ptr}, i32 {w})"]
        else:
            out.append(f"{indent}call void @{STORES[t]}(ptr {ptr}, {t} {v})")
        return True
    return False


CALL = re.compile(r"^(\s*)(?:(" + NAME + r") = )?(?:tail |musttail |notail )?call ")


def _indirect(line, fresh, out) -> bool:
    """A call through a pointer, rewritten through port_icall and the slots; False for any other line."""
    m = CALL.match(line)
    if not m:
        return False
    indent, result = m.groups()
    rest = line[m.end():]
    callee = re.search(r"(" + NAME + r")\(|(inttoptr \(i32 -?\d+ to ptr\))\(", rest)
    if not callee or "@" in rest[:callee.start()]:
        return False
    head = rest[:callee.start()].strip()  # attributes and the return type, or a function type
    ret = head[:head.index("(")].strip().split()[-1] if "(" in head else head.split()[-1]
    fp = callee.group(1) or callee.group(2)
    open_paren = callee.end() - 1
    close = balanced(rest, open_paren)
    args = [a for a in split_top(rest[open_paren + 1:close])]
    if len(args) > SLOTS:
        return False
    for i, a in enumerate(args):
        t, v = parse_arg(a)
        w = _value_to_i64(out, fresh, t, v, "zeroext" in a)
        if w is None:
            return False
        s = fresh("s")
        out += [f"{indent}{s} = getelementptr [{SLOTS + 1} x i64], ptr %port_slots, i32 0, i32 {i}",
                f"{indent}store i64 {w}, ptr {s}, align 8"]
    out.append(f"{indent}call void @port_icall(ptr {fp}, ptr %port_slots, i32 {len(args)})")
    if result and ret != "void":
        s, r = fresh("s"), fresh("r")
        out += [f"{indent}{s} = getelementptr [{SLOTS + 1} x i64], ptr %port_slots, i32 0, i32 {SLOTS}",
                f"{indent}{r} = load i64, ptr {s}, align 8"]
        if _i64_to(out, fresh, r, ret, result) is None:
            return False
    return True


DEFINE = re.compile(r"^define (.*?)@([\w.$]+)\((.*)\)(.*)\{\s*$")


def _universal(define_line: str):
    """The universal entry of a function, `F.port_u(ptr slots, i32 count)`, and the function's name; None for a
    function it can't call (an aggregate in its signature)."""
    m = DEFINE.match(define_line)
    if not m:
        return None
    pre, name, params_text, _ = m.groups()
    if name.startswith("llvm.") or ".port_" in name:
        return None
    ret = pre.split()[-1]
    variadic, params = False, []
    for p in split_top(params_text):
        if p == "...":
            variadic = True
            continue
        words = p.split()
        decl = " ".join(words[:-1]) if words[-1].startswith("%") else p  # the type and attributes, not the name
        params.append((words[0], decl))
    if len(params) > SLOTS:
        return None
    fresh = Fresh()
    body, values = [], []
    for i, (t, decl) in enumerate(params):
        s, raw = fresh("s"), fresh("v")
        body += [f"  {s} = getelementptr i64, ptr %slots, i32 {i}", f"  {raw} = load i64, ptr {s}, align 8"]
        if i < 4:  # not passed: what its register word holds, as on MIPS
            rw, rx = fresh("w"), fresh("w")
            body += [f"  {rw} = call i32 @port_ld32(ptr inttoptr (i32 {REGS + 4 * i} to ptr))",
                     f"  {rx} = zext i32 {rw} to i64"]
        else:
            rx = "0"
        passed, chosen = fresh("c"), fresh("c")
        body += [f"  {passed} = icmp ult i32 {i}, %count", f"  {chosen} = select i1 {passed}, i64 {raw}, i64 {rx}"]
        v = _i64_to(body, fresh, chosen, t)
        if v is None:
            return None
        values.append(f"{decl} {v}")
    call = f"{ret} ({', '.join([t for t, _ in params] + ['...'])})" if variadic else ret
    if variadic:  # the slots past its fixed arguments, as words in its varargs buffer (big-endian, _va_slots)
        for i in range(len(params), min(len(params) + 8, SLOTS)):
            sl, raw, w = fresh("s"), fresh("v"), fresh("w")
            body += [f"  {sl} = getelementptr i64, ptr %slots, i32 {i}", f"  {raw} = load i64, ptr {sl}, align 8",
                     f"  {w} = trunc i64 {raw} to i32"]
            values.append(f"i32 {_swapped(body, fresh, '  ', 'i32', w)}")
    if ret == "void":
        body.append(f"  call {call} @{name}({', '.join(values)})")
        w, x = fresh("w"), fresh("w")
        body += [f"  {w} = call i32 @port_ld32(ptr inttoptr (i32 {REGS + 16} to ptr))", f"  {x} = zext i32 {w} to i64"]
        result = x
    else:
        r = fresh("r")
        body.append(f"  {r} = call {call} @{name}({', '.join(values)})")
        result = _value_to_i64(body, fresh, ret, r, "zeroext" in pre.split())  # a u8 or u16 comes back as one
        if result is None:
            return None
    s = fresh("s")
    body += [f"  {s} = getelementptr i64, ptr %slots, i32 {SLOTS}", f"  store i64 {result}, ptr {s}, align 8",
             "  ret void"]
    return name, f"define internal void @{name}.port_u(ptr %slots, i32 %count) {{\n" + "\n".join(body) + "\n}"


def rewrite(text: str, fixed: dict, tag: str) -> str:
    """The browser build's IR of one file (after beir.py's own rewriting): `fixed` gives the functions' console
    addresses, `tag` names this file's registration."""
    lines = text.split("\n")
    out, fresh = [], Fresh()
    universal, registered = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("define "):
            out.append(line)
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end] != "}":
            end += 1
        body = lines[i + 1:end]
        native = _va_slots(body)
        new_body, icalls = [], False
        for b in body:
            if _memory(b, native, fresh, new_body):
                continue
            if _indirect(b, fresh, new_body):
                icalls = True
                continue
            if _swap_varargs(b, fresh, new_body):
                continue
            new_body.append(b)
        out.append(line)
        if icalls:
            out.append(f"  %port_slots = alloca [{SLOTS + 1} x i64], align 8")
        out.extend(new_body)
        out.append("}")
        if (u := _universal(line)):
            name, define = u
            universal.append(define)
            address = fixed.get(name, (None,))[0]
            if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", name)):
                address = int(auto.group(1), 16)
            registered.append((address or 0, name))
        i = end + 1
    out += universal
    have = set(re.findall(r"^declare [^@]*@([\w.$]+)\(", text, re.M))
    out += [d for d in HELPERS.strip().split("\n") if re.search(r"@([\w.$]+)\(", d).group(1) not in have]
    if registered:
        entries = ", ".join(f"{{ i32, ptr, ptr }} {{ i32 {a if a < 0x80000000 else a - (1 << 32)}, ptr @{n}, "
                            f"ptr @{n}.port_u }}" for a, n in registered)
        out += [f"@port_reg.{tag} = internal constant [{len(registered)} x {{ i32, ptr, ptr }}] [{entries}], align 4",
                f"define internal void @port_reg_ctor.{tag}() {{",
                f"  call void @port_register(ptr @port_reg.{tag}, i32 {len(registered)})", "  ret void", "}",
                "@llvm.global_ctors = appending global [1 x { i32, ptr, ptr }] "
                f"[{{ i32, ptr, ptr }} {{ i32 65535, ptr @port_reg_ctor.{tag}, ptr null }}]"]
    return "\n".join(out) + "\n"
