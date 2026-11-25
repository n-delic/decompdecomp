"""What a polisher must leave alone in matched C: the m2c prelude and declarations the body uses, and the constructs
that are there only for the match.

Of one N64 game's 91 refused polishes, 45 were compile errors, most because the helper deleted the m2c prelude
(`#ifndef M2C_DEFINES` ... `typedef s32 M2C_UNK;`) while the body still used M2C_FIELD or M2C_UNK; the other 46 no
longer matched because helpers unwrapped `do { } while (0)`, turned goto loops into for loops or merged temporaries.
`restore` puts back what the body still needs (a tidier file without it never compiled, so nothing is lost by
keeping it), and `constructs`/`brief`/`changed` name the scaffolding in the brief and in a refusal.
"""

from __future__ import annotations

import re

PRELUDE = re.compile(r"^[ \t]*#ifndef M2C_DEFINES\b.*?^[ \t]*#endif[^\n]*\n?", re.M | re.S)
# One-line top-level declarations a body may need: extern/typedef/prototypes, and plain #defines.
DECLARATION = re.compile(r"^(?:extern\b[^;{}]*;|typedef\b[^;{}]*;|#define[ \t]+\w+[^\n]*|"
                         r"[A-Za-z_][\w \t\*]*\b\w+[ \t]*\([^;{}]*\)[ \t]*;)[ \t]*$", re.M)
DECLARED = re.compile(r"#define[ \t]+(\w+)|(\w+)\s*(?:\[[^\]]*\]\s*)*(?:\([^()]*\)\s*)?;\s*$")

# (label, pattern): code there for the match. Counted in the matched C and in a rewrite.
CONSTRUCTS = (
    ("do { ... } while (0) wrappers", re.compile(r"\bwhile\s*\(\s*0\s*\)")),
    ("goto loops and their labels", re.compile(r"\bgoto\s+\w+\s*;|^[ \t]*[A-Za-z_]\w*:(?!:)", re.M)),
    ("register hints", re.compile(r"\bregister\b")),
    ("volatile locals and frame fillers", re.compile(r"\bvolatile\b")),
    ("__repair_* locals", re.compile(r"\b__repair_\w*")),
    ("M2C_* macros", re.compile(r"\bM2C_\w+")),
    ("empty statements and if (0) blocks", re.compile(r"^[ \t]*;[ \t]*$|\bif\s*\(\s*0\s*\)", re.M)),
)


def _code(text: str) -> str:
    """Text without comments, so a comment mentioning goto counts nothing."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)


def constructs(source: str) -> dict[str, int]:
    code = _code(source)
    found = {label: len(p.findall(code)) for label, p in CONSTRUCTS}
    return {label: n for label, n in found.items() if n}


def brief(source: str) -> str:
    """The brief's line naming the match-critical constructs in the matched C, or ""."""
    found = constructs(PRELUDE.sub("", source))
    lines = []
    if PRELUDE.search(source):
        lines.append("- keep the m2c prelude (#ifndef M2C_DEFINES ... #endif) and the declarations above the function: "
                     "the body uses them, and removing them is a compile error;")
    if found:
        lines.append("- these are there for the match; keep them as they are: " + ", ".join(
            f"{label} ({n})" for label, n in found.items()) + ";")
    return "\n".join(lines) + ("\n" if lines else "")


def changed(original: str, source: str) -> str:
    """Which scaffolding a refused rewrite changed (its count differs from the matched C's), or ""."""
    before, after = constructs(PRELUDE.sub("", original)), constructs(PRELUDE.sub("", source))
    diffs = [f"{label}: {before.get(label, 0)} -> {after.get(label, 0)}"
             for label in dict.fromkeys([*before, *after]) if before.get(label, 0) != after.get(label, 0)]
    return ("It changed constructs that are there for the match; put them back: " + "; ".join(diffs) + ".") if diffs else ""


def _uses(code: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", code) is not None


def restore(original: str, source: str, name: str) -> tuple[str, list[str]]:
    """`source` with the m2c prelude and the one-line declarations of `original` it dropped but still uses put back
    at its top, and what was put back. Declarations the rewrite still has, or names it no longer uses (renamed), are
    left alone."""
    from ..forks.csource import find
    code = _code(source)
    put = []
    prelude = PRELUDE.search(original)
    if prelude and not PRELUDE.search(source) and re.search(r"\bM2C_\w+", code):
        put.append(prelude[0].rstrip("\n"))
    span = find(original, name)
    head = PRELUDE.sub("", original[:span[0]] if span else "")
    declared_here = {m[1] or m[2] for m in (DECLARED.search(line) for line in DECLARATION.findall(source)) if m}
    for line in DECLARATION.findall(head):
        m = DECLARED.search(line)
        ident = m and (m[1] or m[2])
        if not ident or ident == name or ident in declared_here or line.strip() in source:
            continue
        if _uses(code, ident):
            put.append(line.strip())
    if not put:
        return source, []
    return "\n".join(put) + "\n" + source, put
