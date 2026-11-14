"""Rewrite wasm32 LLVM IR so its initialised data is stored big-endian (port/wasm).

Every global with an initialiser becomes a packed struct of byte runs (each number in big-endian order) and the
pointer-sized expressions the linker fills (left as they are: a pass after linking swaps each relocated slot).
Symbols given as numbers (ROM offsets) become those numbers.

usage: beir.py in.ll out.ll [--absolute name=value ...] [--alias name=base+offset ...]
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

INT = re.compile(r"i(\d+)$")


class Types:
    def __init__(self, text):
        self.named = {}
        for m in re.finditer(r"^(%[\w.$\"-]+) = type (.*)$", text, re.M):
            self.named[m.group(1)] = m.group(2).strip()
        self.cache = {}

    def layout(self, t):
        """(size, align) of type text `t`."""
        t = t.strip()
        if t in self.cache:
            return self.cache[t]
        if (m := INT.match(t)):
            n = max(1, (int(m.group(1)) + 7) // 8)
            r = (n, n if n in (1, 2, 4, 8) else 8)
        elif t == "float":
            r = (4, 4)
        elif t == "double":
            r = (8, 8)
        elif t == "ptr" or t.endswith("*"):
            r = (4, 4)
        elif t.startswith("["):
            n, elem = split_array(t)
            s, a = self.layout(elem)
            r = (n * s, a)
        elif t.startswith("<{"):
            fields = split_list(t[2:-2])
            r = (sum(self.layout(f)[0] for f in fields), 1)
        elif t.startswith("{"):
            r = struct_layout([self.layout(f) for f in split_list(t[1:-1])])[:2]
        elif t.startswith("%"):
            body = self.named[t]
            if body == "opaque":
                r = (0, 1)
            else:
                r = self.layout(body)
        else:
            raise ValueError(f"type {t!r}")
        self.cache[t] = r
        return r

    def fields(self, t):
        t = t.strip()
        while t.startswith("%"):
            t = self.named[t]
        return t


def struct_layout(parts):
    off, align, offs = 0, 1, []
    for s, a in parts:
        off = (off + a - 1) // a * a
        offs.append(off)
        off += s
        align = max(align, a)
    return ((off + align - 1) // align * align, align, offs)


def split_array(t):
    m = re.match(r"\[\s*(\d+)\s+x\s+(.*)\]$", t, re.S)
    return int(m.group(1)), m.group(2)


def split_list(s):
    """Top-level comma-separated items of `s` (brackets, braces, parens and strings respected)."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(s):
        ch = s[i]
        if ch == '"':
            j = i + 1
            while s[j] != '"':
                j += 1
            cur.append(s[i:j + 1])
            i = j + 1
            continue
        if ch in "[{(<":
            depth += 1
        elif ch in "]})>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def take_type(s):
    """(type, rest) from the start of `s`."""
    s = s.lstrip()
    if s[0] in "[{<":
        depth = 0
        for i, ch in enumerate(s):
            if ch in "[{<":
                depth += 1
            elif ch in "]}>":
                depth -= 1
                if depth == 0:
                    return s[:i + 1], s[i + 1:]
        raise ValueError(s)
    m = re.match(r"(%[\w.$\"-]+|i\d+|float|double|ptr)\s*", s)
    return m.group(1), s[m.end():]


def unescape(body):
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] == "\\" and body[i + 1] == "\\":
            out.append(0x5C)
            i += 2
        elif body[i] == "\\":
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(ord(body[i]))
            i += 1
    return bytes(out)


class Image:
    """A global's initial bytes, big-endian, with expressions the linker fills."""

    def __init__(self):
        self.parts = []  # bytes or (type, expr)
        self.size = 0

    def raw(self, b):
        if self.parts and isinstance(self.parts[-1], bytearray):
            self.parts[-1] += b
        else:
            self.parts.append(bytearray(b))
        self.size += len(b)

    def expr(self, t, e, size):
        self.parts.append((t, e))
        self.size += size

    def pad(self, n):
        if n > 0:
            self.raw(bytes(n))


def float_bytes(t, v):
    v = v.strip()
    if v.startswith("0x"):
        d = struct.unpack(">d", bytes.fromhex(v[2:].rjust(16, "0")))[0]
    else:
        d = float(v)
    return struct.pack(">f" if t == "float" else ">d", d)


def address(types, v):
    """The number a constant pointer expression stands for, when it's built on a fixed address; else None."""
    v = v.strip()
    m = re.fullmatch(r"inttoptr \(i32 (-?\d+) to ptr\)", v)
    if m:
        return int(m.group(1)) & 0xFFFFFFFF
    m = re.fullmatch(r"ptrtoint \(ptr (.*) to i32\)", v)
    if m:
        return address(types, m.group(1))
    m = re.fullmatch(r"getelementptr (?:inbounds |nuw |nusw )*\((.*)\)", v, re.S)
    if m:
        parts = split_list(m.group(1))
        base_type = parts[0]
        base = address(types, parts[1][len("ptr "):]) if parts[1].startswith("ptr ") else None
        if base is None:
            return None
        t, offset = base_type, 0
        for k, idx in enumerate(parts[2:]):
            n = int(idx.split()[-1])
            if k == 0:
                offset += n * types.layout(t)[0]
                continue
            body = types.fields(t)
            if body.startswith("["):
                _, t = split_array(body)
                offset += n * types.layout(t)[0]
            else:
                packed = body.startswith("<{")
                fields = split_list(body[2:-2] if packed else body[1:-1])
                offs = (list(__import__("itertools").accumulate([0] + [types.layout(f)[0] for f in fields]))
                        if packed else struct_layout([types.layout(f) for f in fields])[2])
                offset += offs[n]
                t = fields[n]
        return (base + offset) & 0xFFFFFFFF
    return None


def emit(types, img, t, v):
    t = t.strip()
    v = v.strip()
    size, _ = types.layout(t)
    start = img.size
    if v in ("zeroinitializer", "undef", "poison", "null"):
        img.pad(size)
    elif (m := INT.match(t)):
        n = (int(m.group(1)) + 7) // 8
        if re.fullmatch(r"-?\d+", v) or v in ("true", "false"):
            x = {"true": 1, "false": 0}.get(v, None)
            x = int(v) if x is None else x
            img.raw((x & ((1 << (8 * n)) - 1)).to_bytes(n, "big"))
        elif (a := address(types, v)) is not None:
            img.raw((a & ((1 << (8 * n)) - 1)).to_bytes(n, "big"))
        else:
            img.expr(t, v, size)  # a constant expression (an address as a number): the linker fills it
    elif t in ("float", "double"):
        img.raw(float_bytes(t, v))
    elif t == "ptr":
        a = address(types, v)
        if a is not None:
            img.raw(a.to_bytes(4, "big"))
        else:
            img.expr("ptr", v, 4)
    else:
        body = types.fields(t)
        if body.startswith("["):
            n, elem = split_array(body)
            if v.startswith('c"'):
                img.raw(unescape(v[2:-1]))
            else:
                items = split_list(v[1:-1])
                for item in items:
                    et, ev = take_type(item)
                    emit(types, img, et, ev)
        elif body.startswith("<{") or body.startswith("{"):
            packed = body.startswith("<{")
            ftypes = split_list(body[2:-2] if packed else body[1:-1])
            inner = v[2:-2] if v.startswith("<{") else v[1:-1]
            items = split_list(inner)
            if packed:
                for item in items:
                    et, ev = take_type(item)
                    emit(types, img, et, ev)
            else:
                _, _, offs = struct_layout([types.layout(f) for f in ftypes])
                for off, item in zip(offs, items):
                    img.pad(start + off - img.size)
                    et, ev = take_type(item)
                    emit(types, img, et, ev)
        else:
            raise ValueError(f"constant {t} {v[:80]}")
    img.pad(start + size - img.size)


GLOBAL = re.compile(r"^(@[\w.$\"-]+) = ((?:[a-z_]+ )*?)(global|constant) (.*)$")


def rewrite_global(types, line):
    m = GLOBAL.match(line)
    if not m or m.group(1).startswith("@llvm.") or "external" in m.group(2).split():
        return line
    name, pre, kind, rest = m.groups()
    t, rest = take_type(rest)
    items = split_list(rest)
    init, tail = items[0], items[1:]
    if not init:
        return line
    img = Image()
    emit(types, img, t, init)
    if not any(isinstance(p, tuple) for p in img.parts) and all(not any(p) for p in img.parts):
        return line  # all zeroes: the same bytes either way
    ftypes, values = [], []
    for p in img.parts:
        if isinstance(p, tuple):
            ftypes.append(p[0])
            values.append(f"{p[0]} {p[1]}")
        else:
            body = "".join(chr(c) if 32 <= c < 127 and chr(c) not in '"\\' else f"\\{c:02X}" for c in p)
            ftypes.append(f"[{len(p)} x i8]")
            values.append(f'[{len(p)} x i8] c"{body}"')
    if not any(a.startswith("align") for a in tail):
        tail = [f"align {types.layout(t)[1]}"] + tail
    nt = "<{ " + ", ".join(ftypes) + " }>"
    nv = "<{ " + ", ".join(values) + " }>"
    return f"{name} = {pre}{kind} {nt} {nv}" + "".join(", " + x for x in tail)


def plain(ps):
    """Parameters as a declaration writes them (without the by-value sizes the signatures carry)."""
    return re.sub(r"#byval\d+", "", ps)


REF = re.compile(r"@([\w.$]+)(?![\w.$])")


def pin(text, fixed, functions):
    """Every global the project has an address for is used at that address: data symbols, and functions where the
    C takes their address (a direct call stays a call). Their declarations go; a definition here is kept, unused,
    under another name."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"^@([\w.$]+) = (.*)$", line)
        if m and m.group(1) in fixed:
            name, rest = m.groups()
            words = rest.split()
            if "external" in words[:4] and name not in functions:
                continue  # data at a fixed address: nothing to declare
            if "external" not in words[:4]:
                line = f"@{name}.port_unused = internal " + re.sub(r"^(?:[a-z_]+ )*?(?=global|constant)", "", rest, count=1)
        head = re.match(r"^(define|declare) ", line)

        def swap(mm, line=line, head=head):
            name = mm.group(1)
            if name not in fixed:
                return mm.group(0)
            addr, kind = fixed[name]
            after = line[mm.end():mm.end() + 1]
            if kind == "func" or name in functions:
                if after == "(":
                    return mm.group(0)  # a call, or its define/declare line
            return f"inttoptr (i32 {addr} to ptr)"
        if not line.startswith("@") or " = " in line:
            line = REF.sub(swap, line) if not (m and m.group(1) in fixed and "port_unused" not in line) else line
        out.append(line)
    return "\n".join(out)


def volatile(lines):
    """Every load and store volatile. Memory is big-endian here, and LLVM's instruction selection narrows a load
    whose value is then masked (`x & 0xFFFF`, `(u8)x`) to a smaller load at the variable's own address, which
    is the low half only on a little-endian machine: a racing game's vertex index came back 0. Volatile accesses
    are never narrowed, widened or merged."""
    out = []
    for line in lines:
        line = re.sub(r"(= load )(?!volatile|atomic)", r"\1volatile ", line)
        line = re.sub(r"^(\s+store )(?!volatile|atomic)", r"\1volatile ", line)
        out.append(line)
    return out


def main():
    src, dst, *opts = sys.argv[1:]
    absolute, aliases, functions, signatures, fixed, renames = {}, [], set(), {}, {}, {}
    web = False
    it = iter(opts)
    for o in it:
        if o == "--web":  # the browser build's IR (webir.py)
            web = True
        elif o == "--absolute":
            k, v = next(it).split("=")
            absolute[k] = int(v, 0)
        elif o == "--alias":
            aliases.append(next(it))
        elif o == "--signatures":
            for row in open(next(it)).read().splitlines():
                n, r, ps = row.split("\t")
                signatures[n] = (r, ps)
        elif o == "--fixed":
            for row in open(next(it)).read().splitlines():
                n, a, *kind = row.split()
                fixed[n] = (int(a, 0), kind[0] if kind else "")
        elif o == "--functions":
            functions.update(open(next(it)).read().split())
        elif o == "--renames":  # old new: a call by a function's older name goes to the function by its name now
            renames.update(row.split() for row in open(next(it)).read().splitlines() if row.strip())
    text = open(src).read()
    for old, new in renames.items():
        text = re.sub(rf"@{re.escape(old)}\b", f"@{new}", text)
    if renames:  # one declaration of each
        seen, lines = set(), []
        for line in text.split("\n"):
            dm = re.match(r"^declare .*?@([\w.$]+)\(", line)
            if dm and dm.group(1) in seen:
                continue
            if dm:
                seen.add(dm.group(1))
            lines.append(line)
        text = "\n".join(lines)
    named = {a.split("=")[0] for a in aliases}
    if named:  # a declaration of a name an alias defines here goes
        text = "\n".join(l for l in text.split("\n")
                         if not ((m := re.match(r"^@([\w.$]+) = external ", l)) and m.group(1) in named))
    if fixed:
        for name in set(REF.findall(text)) - set(fixed):  # an automatic name says its address
            if (m := re.fullmatch(r"(D|B|func)_([0-9A-Fa-f]+)", name)):
                fixed[name] = (int(m.group(2), 16), "func" if m.group(1) == "func" else "")
        text = pin(text, fixed, functions)
    types = Types(text)
    out = []
    for line in text.split("\n"):
        if absolute:
            dm = re.match(r"^@([\w.$]+) = external (?:dso_local |hidden )?global", line)
            if dm and dm.group(1) in absolute:
                continue
            for k, v in absolute.items():
                line = re.sub(rf"@{re.escape(k)}\b", f"inttoptr (i32 {v} to ptr)", line)
        fm = re.match(r"^@([\w.$]+) = external (?:dso_local |hidden )?global", line)
        if fm and fm.group(1) in functions:  # a function the C declared as data: in wasm it must be a function
            r, ps = signatures.get(fm.group(1), ("void", ""))
            line = f"declare {r} @{fm.group(1)}({plain(ps)})"
        dm = re.match(r"^declare .*?@([\w.$]+)\(", line)
        if dm and dm.group(1) in signatures:  # one signature everywhere: LLVM adapts each call that differs
            r, ps = signatures[dm.group(1)]
            line = f"declare {r} @{dm.group(1)}({plain(ps)})"
        out.append(rewrite_global(types, line) if line.startswith("@") else line)
    for a in aliases:
        name, rhs = a.split("=")
        base, off = rhs.split("+")
        out.append(f"@{name} = alias i8, getelementptr (i8, ptr @{base}, i32 {int(off, 0)})")
    from adapt import adapt
    out = volatile(out)
    report = []
    byval = {n: {i: int(m.group(1)) for i, p in enumerate(ps.split(", ")) if (m := re.search(r"#byval(\d+)", p))}
             for n, (r, ps) in signatures.items() if "#byval" in ps}
    result = adapt("\n".join(out), report, types, byval)
    if web:
        from webir import rewrite
        result = rewrite(result, fixed, re.sub(r"\W", "_", os.path.basename(dst)))
    open(dst, "w").write(result)
    if report:
        open(dst + ".calls", "w").write("\n".join(report) + "\n")


if __name__ == "__main__":
    main()
