"""Recognize narrow macro forms that cannot change scalar initialization flow.

Do not expand or evaluate arbitrary preprocessor code. Unknown definitions remain
barriers for the bounded CFG check, including definitions that mention a tracked
local through an otherwise ordinary memory alias.
"""
import re

from .csource import _blank
from .integer_repair_support import IDENT

DEFINE = re.compile(rf'(?m)^[ \t]*#[ \t]*define[ \t]+({IDENT})(\([^\n)]*\))?[ \t]*([^\n]*)')


def _groups(text):
    """A bare identifier or adjacent balanced parenthesis groups, not a binary expression."""
    if re.fullmatch(IDENT, text):
        return True
    depth = 0
    groups = 0
    for char in text:
        if char == '(':
            if depth == 0:
                groups += 1
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and not char.isspace():
            return False
    return depth == 0 and 1 <= groups <= 2


def transparent_macros(text, tracked):
    """Pure dereference aliases and exact self-cast forwarders; everything else is unknown."""
    definitions = {}
    for hit in DEFINE.finditer(re.sub(r'\\\r?\n', ' ', text)):
        definitions.setdefault(hit[1], []).append((hit[2], hit[3]))
    safe = set()
    for _ in range(16):
        before = set(safe)
        for name, versions in definitions.items():
            if name in safe:
                continue
            for parameters, raw in versions:
                if any(c in raw for c in "\"'\\#"):
                    break
                body = _blank(raw).strip()
                dependencies = set(re.findall(IDENT, body))
                if parameters is None:
                    # An expression-shaped load cannot add a branch, declaration
                    # or hidden initialization of a tracked scalar.
                    if (not body.startswith('(*') or not body.endswith(')')
                            or re.search(r'[;{}=,?:]|\+\+|--|&&|\|\||\b(?:sizeof|typeof|__typeof__|_Alignof)\b', body)
                            or re.search(rf'\b{IDENT}\s*\(', body)
                            or not _groups(body[2:-1].strip())):
                        break
                else:
                    params = [p.strip() for p in parameters[1:-1].split(',')]
                    if not params or any(not re.fullmatch(IDENT, p) for p in params) or len(set(params)) != len(params):
                        break
                    # ((Record *) same_function(a,b)): each argument is forwarded
                    # once, unchanged; only the result is cast.
                    compact = re.sub(r'\s+', '', body)
                    pattern = rf'\(\({IDENT}\**\){re.escape(name)}\({re.escape(",".join(params))}\)\)'
                    if not re.fullmatch(pattern, compact):
                        break
                    dependencies -= set(params) | {name}
                if dependencies & (set(tracked) | (definitions.keys()-safe)):
                    break
            else:
                safe.add(name)
        if safe == before:
            break
    return safe
