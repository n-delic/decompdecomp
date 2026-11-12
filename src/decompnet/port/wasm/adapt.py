"""Calls whose argument or return types differ from the callee's one signature go through a thunk that converts
them (wasm refuses a direct call of the wrong type; MIPS just passed registers)."""
import re

ATTRS = {"noundef", "signext", "zeroext", "nonnull", "readonly", "writeonly", "noalias", "nocapture", "returned",
         "inreg", "nofree", "nest", "immarg", "noundef", "range"}


def wclass(t):
    t = t.strip()
    if t in ("void",):
        return "void"
    if t in ("float",):
        return "f32"
    if t in ("double",):
        return "f64"
    if t == "i64":
        return "i64"
    if t == "ptr" or re.fullmatch(r"i(1|8|16|32)", t):
        return "i32"
    return "other:" + t


def split_top(text):
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "({[<":
            depth += 1
        elif ch in ")}]>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def balanced(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_arg(a):
    """(type, value) of a call argument."""
    a = a.strip()
    if a[0] in "[{<%" and a[0] != "%":
        close = {"[": "]", "{": "}", "<": ">"}[a[0]]
        depth = 0
        for i, ch in enumerate(a):
            depth += ch == a[0]
            depth -= ch == close
            if depth == 0:
                t, rest = a[:i + 1], a[i + 1:]
                break
    else:
        t, _, rest = a.partition(" ")
    rest = rest.strip()
    while True:
        m = re.match(r"(\w+)(\([^)]*\))?\s+", rest)
        if m and (m.group(1) in ATTRS or m.group(1) in ("align", "byval", "sret", "dereferenceable")) :
            rest = rest[m.end():]
            if m.group(1) == "align":
                rest = re.sub(r"^\d+\s+", "", rest)
            continue
        break
    return t, rest


def zero(t):
    c = wclass(t)
    return {"f32": "0.0", "f64": "0.0"}.get(c, "null" if t == "ptr" else "0")


def convert(lines, n, value, src, dst):
    """Instructions (appended to `lines`) turning `value` of type src into dst; the name of the result."""
    if src == dst:
        return value
    tmp = f"%c{n}"
    ints = re.compile(r"i(\d+)$")
    s_int, d_int = ints.match(src), ints.match(dst)
    if src == "ptr" and d_int:
        lines.append(f"  {tmp}a = ptrtoint ptr {value} to i32")
        return convert(lines, n + 100, f"{tmp}a", "i32", dst)
    if dst == "ptr" and s_int:
        v = convert(lines, n + 100, value, src, "i32")
        lines.append(f"  {tmp} = inttoptr i32 {v} to ptr")
        return tmp
    if s_int and d_int:
        a, b = int(s_int.group(1)), int(d_int.group(1))
        op = "trunc" if a > b else ("zext" if a == 1 else "sext")
        lines.append(f"  {tmp} = {op} {src} {value} to {dst}")
        return tmp
    if src in ("float", "double") and dst in ("float", "double"):
        lines.append(f"  {tmp} = {'fpext' if src == 'float' else 'fptrunc'} {src} {value} to {dst}")
        return tmp
    # between integers and floating point: the same bits, sized to fit
    size = {"float": 32, "double": 64}
    if src in size:
        lines.append(f"  {tmp}b = bitcast {src} {value} to i{size[src]}")
        return convert(lines, n + 200, f"{tmp}b", f"i{size[src]}", dst)
    if dst in size:
        v = convert(lines, n + 300, value, src, f"i{size[dst]}")
        lines.append(f"  {tmp} = bitcast i{size[dst]} {v} to {dst}")
        return tmp
    return zero(dst)


# MIPS's argument registers a0-a3 as four words of memory (below the game's data, above the boot stack): a call
# that passes more arguments than its callee declares leaves them here, and a call that leaves arguments out finds
# them here, as they'd have stayed in the registers
REGS = 0x00090000


def reg(i):
    return f"inttoptr (i32 {REGS + 4 * i} to ptr)"


REPORT = None
CALLER = [""]


def word_thunk(tname, name, ret, params, pbyval, cret, args, abyval):
    """A thunk that lays the call's arguments out as MIPS o32 argument words (a struct passed by value as its
    words, read from memory, big-endian as the registers held them) and takes the callee's parameters from them."""
    head = ", ".join(f"{'ptr' if abyval[i] else t} %a{i}" for i, (t, _) in enumerate(args))
    body, words, n = [], [], 0

    def tmp():
        nonlocal n
        n += 1
        return f"%w{n}"
    for i, (t, _) in enumerate(args):
        v = f"%a{i}"
        if abyval[i]:
            for j in range((abyval[i] + 3) // 4):
                q, w = tmp(), tmp()
                body.append(f"  {q} = getelementptr i8, ptr {v}, i32 {4 * j}")
                body.append(f"  {w} = load i32, ptr {q}, align 1")
                words.append(w)
        elif t in ("i64", "double"):
            if len(words) % 2:
                words.append("0")
            x = v
            if t == "double":
                x = tmp()
                body.append(f"  {x} = bitcast double {v} to i64")
            hi, hi2, lo = tmp(), tmp(), tmp()
            body.append(f"  {hi} = lshr i64 {x}, 32")
            body.append(f"  {hi2} = trunc i64 {hi} to i32")
            body.append(f"  {lo} = trunc i64 {x} to i32")
            words += [hi2, lo]
        else:
            words.append(convert(body, 500 + i, v, t, "i32"))
    values, w = [], 0
    for i, p in enumerate(params):
        if i in pbyval:
            count = (pbyval[i] + 3) // 4
            buf = tmp()
            body.append(f"  {buf} = alloca [{count} x i32], align 8")
            for j in range(count):
                q = tmp()
                body.append(f"  {q} = getelementptr i8, ptr {buf}, i32 {4 * j}")
                body.append(f"  store i32 {words[w + j] if w + j < len(words) else 0}, ptr {q}, align 4")
            w += count
            values.append(f"ptr {buf}")
        elif p in ("i64", "double"):
            w += w % 2
            hi = words[w] if w < len(words) else "0"
            lo = words[w + 1] if w + 1 < len(words) else "0"
            a, b, c, d = tmp(), tmp(), tmp(), tmp()
            body += [f"  {a} = zext i32 {hi} to i64", f"  {b} = shl i64 {a}, 32", f"  {c} = zext i32 {lo} to i64",
                     f"  {d} = or i64 {b}, {c}"]
            if p == "double":
                e = tmp()
                body.append(f"  {e} = bitcast i64 {d} to double")
                d = e
            values.append(f"{p} {d}")
            w += 2
        else:
            values.append(f"{p} " + convert(body, 700 + i, words[w] if w < len(words) else "0", "i32", p))
            w += 1
    if ret == "void":
        body.append(f"  call void @{name}({', '.join(values)})")
        body.append("  ret void" if cret == "void" else f"  ret {cret} {zero(cret)}")
    else:
        body.append(f"  %r = call {ret} @{name}({', '.join(values)})")
        if cret == "void":
            body.append("  ret void")
        else:
            body.append(f"  ret {cret} " + convert(body, 900, "%r", ret, cret))
    return f"define internal {cret} @{tname}({head}) {{\n" + "\n".join(body) + "\n}"


def byval_size(types, arg_text):
    m = re.search(r"byval\((.*?)\)\s", arg_text + " ")
    return types.layout(m.group(1))[0] if (m and types) else 0


def adapt(text, report=None, types=None, byval=None):
    global REPORT
    REPORT = report
    byval = byval or {}
    sigs = {}
    for m in re.finditer(r"^(?:define|declare) [^@\n]*?([\w%.<>{}\[\]* ]+?) @([\w.$]+)\(", text, re.M):
        open_paren = text.index("(", m.end() - 1)
        close = balanced(text, open_paren)
        params = []
        variadic = False
        for p in split_top(text[open_paren + 1:close]):
            if p == "...":
                variadic = True
                continue
            params.append(parse_arg(p + " %x")[0] if " " not in p.strip() else parse_arg(p)[0])
        ret = m.group(1).split()[-1]
        sigs[m.group(2)] = (ret, params, variadic)
    thunks, out_lines = {}, []
    current = []  # the enclosing function's parameters: [(type, name)]
    for line in text.split("\n"):
        stripped = line.lstrip()
        if line.startswith("define "):
            dm = re.search(r"@([\w.$]+)\(", line)
            CALLER[0] = dm.group(1)
            close_d = balanced(line, dm.end() - 1)
            current = []
            for i, p in enumerate(split_top(line[dm.end():close_d])):
                if p == "...":
                    continue
                t = p.split()[0]
                nm = p.split()[-1] if p.split()[-1].startswith("%") else f"%{i}"
                current.append((t, nm))
        cm = re.search(r"\bcall ([^@]*?)@([\w.$]+)\(", line)
        if not cm or cm.group(2) not in sigs or stripped.startswith(("define", "declare")):
            out_lines.append(line)
            continue
        name = cm.group(2)
        ret_text = cm.group(1).strip()
        if ret_text.endswith(")"):
            ret_text = ret_text[:ret_text.rindex("(")].strip()
        cret = ret_text.split()[-1]
        open_paren = cm.end() - 1
        close = balanced(line, open_paren)
        raw_args = split_top(line[open_paren + 1:close])
        args = [parse_arg(a) for a in raw_args]
        abyval = [byval_size(types, a) if "byval(" in a else 0 for a in raw_args]
        ret, params, variadic = sigs[name]
        pbyval = byval.get(name, {})
        if (any(abyval) or pbyval) and not variadic:
            key = ("words", name, cret, tuple(t for t, _ in args), tuple(abyval))
            if key not in thunks:
                tname = f"{name}.port_words_{len(thunks)}"
                thunks[key] = (tname, word_thunk(tname, name, ret, params, pbyval, cret, args, abyval))
            tname = thunks[key][0]
            plain = ", ".join(("ptr " + v) if abyval[i] else raw_args[i] for i, (t, v) in enumerate(args))
            out_lines.append(line[:cm.start(2) - 1] + "@" + tname + "(" + plain + line[close:])
            continue
        fixed_args = args[:len(params)]
        # exactly the same types: LLVM makes a direct call. Any difference, even one wasm wouldn't see (an i8
        # parameter called with an i32), LLVM turns into a trap or a call through the table: a thunk instead
        same = (cret == ret and len(args) >= len(params) and (variadic or len(args) == len(params))
                and all(a[0] == p for a, p in zip(fixed_args, params)))
        if same and "..." in cm.group(1) and not variadic:
            # The C declared a fixed function variadic (`f(void *, u32, s32, s32, ...)`): the call's explicit type
            # would pass the extra arguments in a varargs buffer the callee never reads. A plain call passes them.
            out_lines.append(line[:cm.start(1)] + f"{cret} " + line[cm.start(2) - 1:])
            continue
        if same or any(wclass(t).startswith("other") for t, _ in args) or wclass(ret).startswith("other"):
            out_lines.append(line)
            continue
        # an argument the call leaves out is, on MIPS, whatever its register still holds: most often the caller's own
        # argument in that place (decompiled C that matches can rely on it). The thunk takes those too.
        missing, loads = [], []
        for i in range(len(args), len(params)):
            if i >= 4:
                break
            if i < len(current):
                missing.append(current[i])
            else:  # not one the caller declares: whatever its register still holds
                tmpname = f"%port_a{i}_{len(out_lines)}"
                loads.append(f"  {tmpname} = load i32, ptr {reg(i)}, align 4")
                missing.append(("i32", tmpname))
        if missing and REPORT is not None:
            REPORT.append(f"{CALLER[0]} calls {name} with {len(args)} of its {len(params)} arguments: "
                          f"the next {len(missing)} come from {CALLER[0]}'s own")
        args_full = args + [(t, v) for t, v in missing]
        key = (name, cret, tuple(t for t, _ in args_full))
        if key not in thunks:
            thunks[key] = f"{name}.port_thunk_{len(thunks)}"
            body = []
            values = []
            for i, p in enumerate(params):
                if i < len(args_full):
                    values.append(f"{p} " + convert(body, i, f"%a{i}", args_full[i][0], p))
                else:
                    values.append(f"{p} {zero(p)}")
            extra = [f"{t} %a{i}" for i, (t, _) in enumerate(args) if i >= len(params)] if variadic else []
            if not variadic:  # arguments the callee doesn't declare stay in their registers for it to pass on
                for i in range(len(params), min(len(args_full), 4)):
                    body.append(f"  store i32 {convert(body, 600 + i, f'%a{i}', args_full[i][0], 'i32')}, ptr {reg(i)}, align 4")
            ftype = f"{ret} ({', '.join(params + (['...'] if variadic else []))})"
            callee = f"{ftype} @{name}" if variadic else f"{ret} @{name}"
            if ret == "void":
                body.append(f"  call {callee}({', '.join(values + extra)})")
                body.append("  ret void" if cret == "void" else f"  ret {cret} {zero(cret)}")
            else:
                body.append(f"  %r = call {callee}({', '.join(values + extra)})")
                if cret == "void":
                    body.append("  ret void")
                else:
                    rv = convert(body, 900, "%r", ret, cret)
                    body.append(f"  ret {cret} {rv}")
            head = ", ".join(f"{t} %a{i}" for i, (t, _) in enumerate(args_full))
            thunks[key] = (thunks[key], f"define internal {cret} @{thunks[key]}({head}) {{\n" + "\n".join(body) + "\n}")
        tname = thunks[key][0]
        newcall = line[:cm.start(2) - 1] + "@" + tname + line[cm.end(2):]
        if missing:  # pass the caller's own arguments for the ones the call leaves out
            open_p = newcall.index("@" + tname + "(") + len(tname) + 1
            close_p = balanced(newcall, open_p)
            inner = newcall[open_p + 1:close_p]
            extra = ", ".join(f"{t} {v}" for t, v in missing)
            newcall = newcall[:open_p + 1] + (inner + ", " if inner.strip() else "") + extra + newcall[close_p:]
        # a variadic call's explicit function type goes: the thunk is a plain function
        newcall = re.sub(r"(\bcall [^@(]*?)\s*\([^@]*\)\s*@" + re.escape(tname), r"\1 @" + tname, newcall)
        out_lines.extend(loads)
        out_lines.append(newcall)
    return "\n".join(out_lines + [t[1] for t in thunks.values()])
