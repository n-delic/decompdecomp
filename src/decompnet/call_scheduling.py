"""Bounded call-argument scheduling probes before random permutation.

A correct argument order can score worse than a reversed one. Try the inline-return
shape and the argument-order hypothesis together, without requiring the intermediate
candidate to improve. These are search hypotheses, not proofs of C equivalence;
only the existing strict judge can accept one.
"""

from __future__ import annotations

import re
from itertools import combinations

MAX_VARIANTS = 12
_NAME = r"[A-Za-z_]\w*"
_FLOAT = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+)(?:[eE][+-]?\d+)?[fF]?")
_NUMBER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*")
_MASK = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_PAIR = re.compile(
    rf"(?m)^(?P<indent>[ \t]+)(?P<ptr>{_NAME})->(?P<field>{_NAME})\s*(?P<op>\+\+|--);"
    rf"\s*\n[ \t]*(?P<callee>{_NAME})\((?P<args>[^();{{}}\n]+)\);")


def variants(source: str, function: str, header: str = "") -> list[str]:
    """Probe adjacent pointer-field increments and direct calls with literal arguments.

    Deliberately narrow: one typed pointer parameter, no nested argument expressions,
    local shadows, directives in the body, or macros among the moved identifiers.
    Unsupported C/C++ is left to agents and the ordinary permuter.
    """
    if "decomp_arg_" in source:
        return []  # don't stack helpers on a previous probe
    masked = _MASK.sub(lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), source)
    if "/*" in masked or "*/" in masked:
        return []  # malformed comments are not safe edit sites
    definition = re.search(
        rf"(?m)^[ \t]*(?:(?:static|inline|extern)\s+)*(?:{_NAME}\s+)+"
        rf"{re.escape(function)}\s*\((?P<params>[^();{{}}]*)\)\s*\{{", masked)
    if not definition:
        return []
    start = definition.end()
    depth, end = 1, start
    while end < len(masked) and depth:
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    if depth or "#" in masked[start:end] or "::" in masked[start:end]:
        return []
    # A single pointer parameter avoids ambiguous types and captures. Keep its type
    # at file scope, where the original function's parameter type is already visible.
    param = re.fullmatch(rf"\s*(?P<type>(?:struct\s+)?{_NAME})\s*\*\s*(?P<ptr>{_NAME})\s*",
                         definition["params"])
    if not param:
        return []
    ptr = param["ptr"]
    body = masked[start:end]
    if re.search(rf"\b{_NAME}\s+\*?\s*{re.escape(ptr)}\s*[=;,)\[]", body):
        return []  # a possible local shadow
    macros = set(re.findall(rf"(?m)^\s*#\s*define\s+({_NAME})", header + "\n" + source))
    out = []
    for pair in _PAIR.finditer(body):
        if pair["ptr"] != ptr or {ptr, param["type"], pair["field"], pair["callee"]} & macros:
            continue
        args = [a.strip() for a in pair["args"].split(",")]
        if not 2 <= len(args) <= 8 or any(a != ptr and not _FLOAT.fullmatch(a) and not _NUMBER.fullmatch(a)
                                        for a in args):
            continue
        floats = [i for i, arg in enumerate(args) if _FLOAT.fullmatch(arg)]
        orders = [args]
        for left, right in combinations(floats, 2):
            swapped = args.copy()
            swapped[left], swapped[right] = swapped[right], swapped[left]
            if swapped not in orders:
                orders.append(swapped)
        for ordered in orders:
            for index in floats:
                helper = f"decomp_arg_{len(out)}"
                while re.search(rf"\b{helper}\b", source + "\n" + header):
                    helper += "_"
                literal = ordered[index]
                ctype = "float" if literal[-1:] in ("f", "F") else "double"
                declaration = (f"static inline {ctype} {helper}({param['type']} *{ptr})\n{{\n"
                               f"    {ptr}->{pair['field']}{pair['op']};\n    return {literal};\n}}\n\n")
                changed = ordered.copy()
                changed[index] = f"{helper}({ptr})"
                replacement = pair["indent"] + pair["callee"] + "(" + ", ".join(changed) + ");"
                lo, hi = start + pair.start(), start + pair.end()
                candidate = (source[:definition.start()] + declaration + source[definition.start():lo]
                             + replacement + source[hi:])
                if candidate not in out:
                    out.append(candidate)
                if len(out) >= MAX_VARIANTS:
                    return out
    return out
