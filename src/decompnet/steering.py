"""Variants that add code which looks dead: classified, never rejected.

Some repairs (a result stored in a local nobody reads, a `do { } while (0)` weight, a repeated test with the same arm
twice) add C with no visible effect, and the score improves anyway. Two things can be true of such a variant, and
they ask different things of whoever reads the code later:

- ORIGINAL: it emits an instruction that lines up with one the target has and its parent lacked. The game's source
  plausibly had something there (a volatile or hardware read, a debug macro, a value a removed path used), so the
  code is evidence, not scaffolding.
- STEERING: it emits nothing, or only instructions the target doesn't have, and the score moved because register
  allocation or scheduling did. It is scaffolding: polishing tries removing it first.

The rule, in two steps (nothing here ever blocks a variant; a byte-identical match is always accepted):

1. `dead_looking(parent, child)`: the child's source adds a statement that looks dead. Conservative on purpose: a
   local that is declared or assigned and never read, a statement with no effect (`(void)x;`, `x + 0;`), a
   `do { } while (0)` wrapper with nothing in it, or an `if` whose two arms are the same statement. Any other
   change (a cast, a type, a reordering) is left alone: it isn't dead-looking, whatever it does to the score.
2. `classify(...)`: the instructions the child has beyond its parent's (opcode and operand shapes, registers and
   symbol names set aside as in `scoring.shape`) are matched against the target instructions the parent lacked.
   One that lines up: ORIGINAL. None: STEERING.

Works on the judge's diff rows `(marker, target, yours)`, so every console and both modes use it: ROM mode (the
`Network._record` of every lane) and project mode (objdiff's rows). Where the parent's rows can't be had (a project
build can't compile a second source beside the one on disk) nothing is tagged: `Network.parent_rows`.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from . import scoring

ORIGINAL = "original"
STEERING = "steering"
LABELS = {ORIGINAL: "matches target instruction (plausibly original)",
          STEERING: "steering only (scaffolding)"}
SNIPPET_CHARS = 70
SHOWN = 3

_KEYWORDS = {"return", "goto", "else", "case", "break", "continue", "typedef", "default", "do", "if", "while", "for",
             "switch", "sizeof"}
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_ASSIGN = re.compile(r"(?:<<|>>|[-+*/%&|^]|(?<![=!<>]))=(?!=)")  # an assignment, plain or compound; not `==` `<=`
_CALL = re.compile(r"\b(?!(?:if|while|for|switch|sizeof|return)\b)[A-Za-z_]\w*\s*\(")
_DECLARATION = re.compile(r"^(?:[A-Za-z_]\w*(?:\s+|\s*\*+\s*))+([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?(?:=\s*(.+))?$")
_DECLARATION_LIST = re.compile(r"^(?:[A-Za-z_]\w*(?:\s+|\s*\*+\s*))+[A-Za-z_]\w*[^(]*,")  # `s32 a, b;`: not judged
_CONTROL = re.compile(r"^(?:if|while|for|switch|else)\b\s*(?:\(.*\))?\s*$")
_LABEL = re.compile(r"^(?:case\b.*|default|[A-Za-z_]\w*)\s*:$")
_EMPTY_DO = re.compile(r"\bdo\s*\{\s*(?:;\s*)*\}\s*while\s*\(\s*0\s*\)")
_SAME_ARMS = re.compile(r"\bif\s*\([^;{}]*\)\s*\{?\s*([^;{}]+;)\s*\}?\s*else\s*\{?\s*\1\s*\}?")


def _code(source: str) -> str:
    return _COMMENT.sub(" ", source or "")


def _bodies(source: str) -> str:
    """What is inside the function bodies of `source`, one per `) {` at file scope: declarations, struct definitions,
    `extern`s and preprocessor lines around them are not statements and never look dead."""
    code, depth, start, out, last = _code(source), 0, None, [], ""
    for at, char in enumerate(code):
        if char == "{":
            if depth == 0 and last == ")":
                start = at + 1
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
            if depth == 0 and start is not None:
                out.append(code[start:at])
                start = None
        if not char.isspace():
            last = char
    return "\n".join(out)


def _statements(body: str, headers: bool = False) -> list[str]:
    """The statements of a function body, whitespace normalized: split at `;`, `{` and `}` outside parentheses and
    outside an initializer list. Local struct, union and enum declarations are dropped whole; control headers
    (`if (c)`, `else`, `do`, `while (c)`, `for (...)`, `case 1:`) and labels are left out: they aren't values or
    effects, but `headers` keeps them for reading what a condition uses (a local read only by an `if` is read)."""
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    body = re.sub(r"\b(?:struct|union|enum)\b[^;{}()]*\{[^{}]*\}[^;{}]*;", " ", body)
    out, depth, current, at = [], 0, [], 0
    while at < len(body):
        char = body[at]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and char == "{" and "".join(current).rstrip().endswith("="):
            close = body.find("}", at)
            current.append(body[at:close + 1] if close >= 0 else body[at:])
            at = close + 1 if close >= 0 else len(body)
            continue
        if depth == 0 and char in ";{}":
            out.append("".join(current))
            current = []
        else:
            current.append(char)
        at += 1
    out.append("".join(current))
    found = []
    for text in out:
        text = re.sub(r"^(?:else\b)?\s*", "", " ".join(text.split()))
        text = re.sub(r"^(?:(?:case\b[^:]*|default|[A-Za-z_]\w*)\s*:(?!:)\s*)+", "", text)  # (a label, then its statement)
        if not text or text == "do" or ((_CONTROL.match(text) or _LABEL.match(text)) and not headers):
            continue
        found.append(text)
    return found


def _has_effect(statement: str) -> bool:
    return bool(_ASSIGN.search(statement) or "++" in statement or "--" in statement or _CALL.search(statement))


def _reads(name: str, body: str) -> int:
    """How often `name` is used in the statements of `body` other than as the target of a plain assignment or in its
    own declaration."""
    word = re.compile(rf"\b{re.escape(name)}\b")
    reads = 0
    for statement in _statements(body, headers=True):
        found = len(word.findall(statement))
        if not found:
            continue
        written = re.match(rf"^(?!(?:return|case|goto)\b)(?:[A-Za-z_][\w\s\*]*?[\s\*])?{re.escape(name)}\s*(?:\[[^\]]*\]\s*)?(?:=(?!=)|$)", statement)
        reads += found - 1 if written else found
    return reads


def _count(pattern: re.Pattern, body: str) -> list[str]:
    return [m.group(0) for m in pattern.finditer(" ".join(body.split()))]


def dead_looking(parent: str, child: str) -> list[str]:
    """The statements the child adds to its parent that look dead (module docstring, step 1): short snippets of C."""
    before, after = _bodies(parent), _bodies(child)
    added = Counter(_statements(after)) - Counter(_statements(before))
    found = []
    for pattern in (_SAME_ARMS, _EMPTY_DO):
        found.extend(_count(pattern, after)[len(_count(pattern, before)):])
    for statement in added.elements():
        if statement.split(" ", 1)[0] in _KEYWORDS:
            continue
        if _DECLARATION_LIST.match(statement):
            continue
        declared = _DECLARATION.match(statement)
        if declared and declared.group(1) not in _KEYWORDS and not statement.startswith(("*", "(")):
            name, init = declared.group(1), declared.group(2)
            if _reads(name, after) == 0 and not (init and _CALL.search(init)):
                found.append(statement)
            continue
        assigned = re.match(r"^([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=(?!=)", statement)
        if assigned:
            name = assigned.group(1)
            is_local = re.search(rf"(?<![\w])(?!(?:else|return|case|goto|do)\b)[A-Za-z_]\w*(?:\s*\*+\s*|\s+)\*?{re.escape(name)}\b\s*(?:=|;|,|\[)",
                                 " ".join(after.split()))
            if is_local and _reads(name, after) == 0 and not _CALL.search(statement):
                found.append(statement)
            continue
        if not _has_effect(statement) or (statement.startswith("*") and not _ASSIGN.search(statement)
                                          and "++" not in statement):
            found.append(statement)  # (a bare `*p`: dead unless it reads a volatile, which the instructions decide)
    return [" ".join(s.split())[:SNIPPET_CHARS] for s in dict.fromkeys(found)]


def _shapes(rows, column: int) -> tuple[Counter, dict[str, str]]:
    found, text = Counter(), {}
    for row in rows:
        shape = scoring.shape(row[column])
        if not shape or shape in scoring._PADDING:
            continue
        found[shape] += 1
        text.setdefault(shape, scoring._NOTE.sub("", row[column]).strip())
    return found, text


def classify(parent_rows, child_rows, parent: str, child: str) -> dict | None:
    """How a variant that adds dead-looking code relates to the target (module docstring, step 2), or None: it adds
    nothing that looks dead, or the rows don't say. {"tag", "added": [C snippets], "insns": [instructions]}."""
    if not parent_rows or not child_rows or not parent or not child:
        return None
    added = dead_looking(parent, child)
    if not added:
        return None
    before, _ = _shapes(parent_rows, 2)
    after, text = _shapes(child_rows, 2)
    target, target_text = _shapes(child_rows, 1)
    extra = after - before
    wanted = target - before
    aligned = extra & wanted
    if aligned:
        shown = [target_text.get(s) or text[s] for s in aligned]
        # a pad local only sizes the frame: every aligned instruction moves the stack pointer
        return {"tag": ORIGINAL, "added": added, "insns": shown[:SHOWN],
                **({"frame": True} if all(re.search(r"\bsp, \$?sp\b", t) for t in shown) else {})}
    return {"tag": STEERING, "added": added, "insns": [text[s] for s in list(extra)[:SHOWN]]}


def encode(found: dict | None) -> str | None:
    return json.dumps(found, separators=(",", ":")) if found else None


def decode(value) -> dict | None:
    try:
        found = json.loads(value) if value else None
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) and found.get("tag") in LABELS else None


def describe(found: dict) -> str:
    """One line for a function's notes and brief."""
    code = "; ".join(f"`{c}`" for c in found.get("added", [])[:SHOWN])
    emits = ", ".join(f"`{i}`" for i in found.get("insns", [])[:SHOWN])
    if found["tag"] == ORIGINAL:
        detail = f"adds {code} and emits {emits}, which the target has" if emits else f"adds {code}"
        why = ("It only sizes the stack frame (a pad local): keep it as one." if found.get("frame") else
               "Keep it unless a real source explains the instruction better.")
        return f"{LABELS[ORIGINAL]}: {detail}. {why}"
    detail = f"adds {code}" + (f" and emits {emits}, which the target lacks" if emits else " and emits no instruction")
    return f"{LABELS[STEERING]}: {detail}; only register allocation or scheduling moved."


def lines_for(net, fn, source: str) -> list[str]:
    """The recorded tags of a function's attempts, newest first, one line each, for those whose added code `source`
    (the function's current C) still contains: its brief and polishing's checklist. At most SHOWN lines."""
    store = net.project_store(fn["project_id"])
    flat = " ".join(_code(source).split())
    out, seen = [], set()
    for row in store.all("SELECT steering FROM attempts WHERE function_id=? AND steering IS NOT NULL ORDER BY id DESC "
                         "LIMIT 40", fn["id"]):
        found = decode(row["steering"])
        if not found or not any(c in flat for c in found.get("added", [])):
            continue
        key = (found["tag"], tuple(found["added"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(describe(found))
        if len(out) == SHOWN:
            break
    return out
