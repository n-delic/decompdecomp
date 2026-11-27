"""One `lui` shared by the accesses of several symbols, written out before m2c reads it.

A compiler that knows two globals sit in one 64 KB page can load the high half once and reach both:

    lui $a2, %hi(D_80100000)
    sw  $a0, %lo(D_80100000)($a2)
    sw  $a1, %lo(D_80100004)($a2)

m2c reads the base register as the `lui`'s symbol and drops the second `%lo` symbol, so the draft stores (or loads)
`D_80100000` twice: a wrong program, not only a bad score. An N64 boot routine that copies a 16-byte block to address
0 drafted all four word stores into the first word, and every attempt that started from the draft inherited that. Each
such access gets its own `lui $at, %hi(...)` here, which m2c reads as the separate global it is.
"""
import re

_PREFIX = r"(?P<prefix>\s*(?:/\*.*?\*/\s*)?)"
_LUI = re.compile(_PREFIX + r"lui\s+(?P<reg>\$\w+),\s*%hi\((?P<sym>[^()]+)\)\s*$")
_USE = re.compile(_PREFIX + r"(?P<op>(?:l[bhw]u?|l[dw]c1|ld|lw[lr]|s[bhwd]|s[dw]c1|sw[lr])\s+\$\w+,\s*"
                  r"%lo\((?P<sym>[^()]+)\))\((?P<reg>\$\w+)\)\s*$")
_ADDRESS = re.compile(_PREFIX + r"(?P<op>addiu\s+\$\w+,\s*)(?P<reg>\$\w+)(?P<tail>,\s*%lo\((?P<sym>[^()]+)\))\s*$")
_BRANCH = re.compile(_PREFIX + r"(?:j|jr|jal|jalr|b[a-z0-9]*)\b")
_WRITES = re.compile(_PREFIX + r"(?!s[bhwd]|s[dw]c1|sw[lr]|b|j)[a-z][\w.]*\s+(?P<reg>\$\w+)\b")


def split(asm: str) -> str:
    """`asm` with every access through another symbol's `lui` given a `lui $at` of its own, or `asm` itself."""
    out: list[str] = []
    held: dict[str, str] = {}  # register -> the symbol whose high half it was last loaded with
    changed = False
    for line in asm.split("\n"):
        use = _USE.match(line)
        address = None if use else _ADDRESS.match(line)
        hit = use or address
        if hit and hit["reg"] != "$at" and held.get(hit["reg"], hit["sym"]) != hit["sym"]:
            high = f"lui $at, %hi({hit['sym']})"
            if use:
                rewritten = f"{use['prefix']}{use['op']}($at)"
            else:
                rewritten = f"{address['prefix']}{address['op']}$at{address['tail']}"
            # a delay slot can't grow: the `lui` goes before its branch
            at = len(out) - 1 if out and _BRANCH.match(out[-1]) else len(out)
            if at == len(out) or "$at" not in out[at]:
                out.insert(at, f"    {high}")
                line = rewritten
                changed = True
        if lui := _LUI.match(line):
            held[lui["reg"]] = lui["sym"]
        elif written := _WRITES.match(line):
            held.pop(written["reg"], None)
        out.append(line)
    return "\n".join(out) if changed else asm
