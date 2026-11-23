"""sigs.py out.txt known.sigs renames.txt files.ll...: each function's one signature: its definition's, else its fullest declaration's."""
import re
import sys
from collections import Counter

HEAD = re.compile(r"^(define|declare) (.*?)@([\w.$]+)\(")
DROP = re.compile(r"\s+(noundef|signext|zeroext|nonnull|readonly|writeonly|noalias|nocapture|returned|inreg|"
                  r"byval\([^)]*\)|sret\([^)]*\)|align \d+|dereferenceable(?:_or_null)?\(\d+\)|%\d+)")


def params_of(line, start):
    """The text between the parenthesis at `start` and its match."""
    depth = 0
    for i in range(start, len(line)):
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
            if depth == 0:
                return line[start + 1:i]
    return None


def split(text):
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


TYPES = None
CALL = re.compile(r"\bcall\s+(?:[\w.]+\s+)*?([\w.*<>\[\]{}]+)\s+(?:\([^)]*\)\s+)?@([\w.$]+)\(")


def call_shape(line):
    """(callee, (return type, each argument's type)) of a call line, or None."""
    m = CALL.search(line)
    if not m:
        return None
    args = params_of(line, m.end() - 1)
    if args is None:
        return None
    return m.group(2), (m.group(1), tuple(a.split()[0] for a in split(args) if a.split()))


def signature(line):
    """(kind, name, ret, params) of a define/declare line; params None for a function with no prototype."""
    m = HEAD.match(line)
    if not m or m.group(3).startswith("llvm."):
        return None
    kind, pre, name = m.groups()
    ret = pre.split()[-1]
    text = params_of(line, m.end() - 1)
    if text is None or text.strip() == "...":
        return kind, name, ret, None
    ps = []
    for p in split(text):
        if "byval(" in p:  # passed by a pointer to a copy in wasm; its size says how many MIPS argument words it took
            t = re.search(r"byval\((.*?)\)(?:\s|$)", p + " ").group(1)
            ps.append(f"ptr#byval{TYPES.layout(t)[0]}" if TYPES else "ptr")
            continue
        p = DROP.sub("", " " + p).strip()
        ps.append(p.split()[0] if p != "..." else p)
    return kind, name, ret, ", ".join(ps)


def main():
    defs, decls, loose, shapes = {}, {}, {}, {}
    global TYPES
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from beir import Types
    renames = dict(row.split() for row in open(sys.argv[3]) if row.strip())
    for f in sys.argv[4:]:
        TYPES = Types(open(f).read())
        for line in open(f):
            if (c := call_shape(line)):
                shapes.setdefault(renames.get(c[0], c[0]), set()).add(c[1])
            if line.startswith(("define", "declare")) and (s := signature(line)):
                kind, name, ret, ps = s
                # The linker sees the renamed symbol. Pool its callers' declarations before selecting an ABI.
                name = renames.get(name, name)
                if ps is None:  # declared with no prototype (`s32 f();`): only its return type is known
                    if kind == "declare":
                        loose.setdefault(name, Counter())[ret] += 1
                elif kind == "define":
                    defs[name] = (ret, ps)
                else:
                    decls.setdefault(name, Counter())[(ret, ps)] += 1
    known = {}  # libultra's own prototypes: they win over what the game's C declares
    for row in open(sys.argv[2]).read().splitlines():  # the console's library prototypes (its port kit's)
        n, r, ps = row.split("\t")
        known[n] = (r, ps)
    with open(sys.argv[1], "w") as out:
        # only where unprototyped declarations disagree on what it returns: one declared only `u32 f();` keeps being
        # called as each call site has it (a builtin the host answers takes the argument the C passes)
        # (or where its callers call it with different shapes, which wasm imports as different functions and the link
        # refuses: a donor's `int f();` placeholder for address 0 called as (ptr) -> i64, (i64) -> void and () -> i32)
        loose = {n: rets for n, rets in loose.items() if len(rets) > 1 or len(shapes.get(n, ())) > 1}
        for name in sorted(set(defs) | set(decls) | set(known) | set(loose)):
            if name in defs:
                ret, ps = defs[name]
            elif name in known:
                ret, ps = known[name]
            elif name not in decls:
                # only unprototyped declarations, of a function no file defines (a racing game's callers declared one
                # outside its layout `s32 f();` and `void f();`, and the link refused the two): one return type for
                # all of them, a value over none, and no parameters known
                ret = max(loose[name], key=lambda r: (r != "void", loose[name][r]))
                ps = ""
            else:
                ret, ps = max(decls[name], key=lambda rp: (len(split(rp[1])), rp[0] != "void", decls[name][rp]))
            out.write(f"{name}\t{ret}\t{ps}\n")


if __name__ == "__main__":
    main()
