"""Warning signs in an unmatched function's saved best, for its brief: advisory only.

Agents have saved bests that score well and are not the game's program: a display-list function's best wrote one
command word twice to the same address and never wrote the next (COMPILER_MATCHING_LEARNINGS.md, "stored saved best
is not the game's program"), others read locals nothing set or made a local `volatile` only to move the schedule.
The next agent starts from the best, so it is told what looks wrong and why.

Each of these is also real game code: stores to a hardware register or a FIFO repeat, original programmers left
uninitialised reads, and `volatile` can be the developer's. So nothing here refuses, demotes or rescores a source,
a match is never looked at (the brief shows warnings only while the function is unmatched), and stores through
`volatile` or to N64 KSEG1 / hardware addresses (0xA0000000 and up) are never flagged.
"""
from __future__ import annotations

import re

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_IDENT = re.compile(r"[A-Za-z_]\w*")
_HARDWARE = re.compile(r"0x[A-Fa-f][0-9A-Fa-f]{7}\b")  # an address at 0xA0000000 or above: KSEG1, I/O
_DECLARATION = re.compile(r"^\s*((?:(?:const|volatile|unsigned|signed|struct\s+\w+|[A-Za-z_]\w*)\s+)+\**\s*"
                          r"(?:volatile\s+)?\**\s*)([A-Za-z_]\w*)\s*(=[^;]*)?;\s*$")
_TYPES = {"u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64", "f32", "f64", "int", "char", "short", "long", "float",
          "double", "void", "unsigned", "signed"}
_KEYWORDS = {"return", "goto", "case", "else", "do", "if", "while", "for", "switch", "sizeof"}
SHOWN = 3


def _bodies(source: str):
    """Each function's body (the text between its braces) in the source."""
    text = _COMMENT.sub(" ", source)
    for m in re.finditer(r"\)\s*\{", text):
        if re.search(r"\b(?:if|while|for|switch)\s*\([^;{}]*$", text[:m.start() + 1][-200:]):
            continue
        before = text[:m.start()]
        if before.count("{") != before.count("}"):
            continue  # a block inside a function, not a function
        depth, end = 0, None
        for k in range(m.end() - 1, len(text)):
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            if depth == 0:
                end = k
                break
        if end is not None:
            yield text[m.end():end]


def _volatile_names(source: str) -> set[str]:
    """Names declared with `volatile` anywhere: stores through them are the program's own."""
    names = set()
    for m in re.finditer(r"\bvolatile\b[^;(){}]*", _COMMENT.sub(" ", source)):
        names.update(_IDENT.findall(m.group(0)))
    return names


def _repeated_stores(body: str, volatile: set[str]) -> list[str]:
    """`x = a; x = b;` with nothing between: the first store is lost, unless the target is volatile or hardware."""
    found = []
    statements = body.split(";")
    for first, second in zip(statements, statements[1:]):
        if any(c in first + second for c in "{}"):
            continue
        a = re.match(r"^\s*([^=;]+?)\s*=(?!=)(.*)$", first, re.S)
        b = re.match(r"^\s*([^=;]+?)\s*=(?!=)(.*)$", second, re.S)
        if not (a and b) or a.group(1).endswith(("<", ">", "!", "+", "-", "*", "/", "&", "|", "^", "%")):
            continue
        left, right = " ".join(a.group(1).split()), " ".join(b.group(1).split())
        if left != right or b.group(1).endswith(("<", ">", "!", "+", "-", "*", "/", "&", "|", "^", "%")):
            continue
        if "volatile" in left or _HARDWARE.search(left) or set(_IDENT.findall(left)) & volatile:
            continue
        if left in " ".join(b.group(2).split()):
            continue  # the second store reads the first (`x = x + 1`)
        found.append(f"`{left}` is stored twice in a row (the first value is never read)")
    return found


def _uninitialised_reads(body: str) -> list[str]:
    """A local declared without a value whose first use reads it."""
    found = []
    lines = body.split("\n")
    for k, line in enumerate(lines):
        m = _DECLARATION.match(line)
        if not m or m.group(3) or m.group(2) in _KEYWORDS:
            continue
        words = m.group(1).split()
        if not words or words[0] in _KEYWORDS or not (set(words) & _TYPES or "*" in m.group(1) or words[0] == "struct"
                                                      or len(words) == 1):
            continue
        name = m.group(2)
        rest = "\n".join(lines[k + 1:])
        use = re.search(rf"\b{re.escape(name)}\b", rest)
        if not use:
            continue
        after, before = rest[use.end():], rest[:use.start()]
        if re.match(r"\s*(?:\[[^\]]*\]\s*)?(?:\.\w+\s*)?=(?!=)", after) or before.rstrip().endswith(("&", "sizeof(")):
            continue  # its first use sets it, takes its address, or measures it
        if re.match(r"\s*(?:\[|\.)", after) or "*" in m.group(1):
            continue  # an array, a structure or a pointer: its first use can write through it
        found.append(f"local `{name}` is read before anything sets it")
    return found


def _volatile_locals(body: str) -> list[str]:
    """A local that is itself volatile (`volatile s32 x;`, `void * volatile p;`): usually there to move the
    schedule. A pointer to volatile memory (`volatile u32 *reg`) is the program's and isn't flagged."""
    found = []
    for line in body.split("\n"):
        m = _DECLARATION.match(line)
        if not m or "volatile" not in m.group(1):
            continue
        qualifiers = m.group(1)
        if "*" in qualifiers and qualifiers.rfind("volatile") < qualifiers.rfind("*"):
            continue  # points to volatile memory
        found.append(f"local `{m.group(2)}` is declared volatile")
    return found


def warnings(source: str | None) -> list[str]:
    """What looks unlike a real program in a source, each as a short phrase; [] when nothing does."""
    if not source:
        return []
    volatile = _volatile_names(source)
    found: list[str] = []
    for body in _bodies(source):
        found += _repeated_stores(body, volatile) + _uninitialised_reads(body) + _volatile_locals(body)
    return list(dict.fromkeys(found))


def brief_lines(fn) -> list[str]:
    """The brief's lines for an unmatched function's saved best; none for a match or a clean source."""
    if fn["status"] == "matched" or not fn["best_score"]:
        return []
    found = warnings(fn["best_source"])
    if not found:
        return []
    more = f" (and {len(found) - SHOWN} more)" if len(found) > SHOWN else ""
    return ["", "Warning signs in the saved best (advisory: real game code does this too, e.g. hardware registers, "
                "but a source scored by bytes can also be another program): " + "; ".join(found[:SHOWN]) + more
            + ". Check these against the target before building on it; the machine draft keeps the program's "
              "stores and reads."]
