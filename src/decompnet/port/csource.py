"""C source files cut by their top-level items, for the port: a file of matched C or ultralib's, as text.

A save's file of matched C can define more than its own function (a racing game's three sound-player setters were
each matched with C defining all three), and the port compiles whole files, so the copies collided at link
(`only`). ultralib's files hold several functions where the port wants one (port/ultralib.extract). Comments,
strings and preprocessor lines are respected; it isn't a C parser, only enough to find where definitions start and
end at the top level.
"""

from __future__ import annotations

import re

DEFINITION = re.compile(r"^(?!\s*(?:if|for|while|switch|return|else)\b)[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*$",
                         re.S)


def items(text: str) -> list[tuple[str, str | None, bool]]:
    """The file's top-level items in order: (text, the function it defines or None, static). Preprocessor lines
    and declarations are items with no function."""
    items, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "#" and (i == 0 or text[i - 1] == "\n"):
            end = i
            while True:  # a directive, with its continuation lines
                end = text.find("\n", end)
                if end < 0:
                    end = n
                    break
                if text[end - 1] != "\\":
                    break
                end += 1
            items.append((text[i:end + 1], None, False))
            i = end + 1
            continue
        start, depth = i, 0
        while i < n:
            c = text[i]
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = n if end < 0 else end + 2
                continue
            if text.startswith("//", i):
                i = text.find("\n", i)
                i = n if i < 0 else i
                continue
            if c in "\"'":
                j = i + 1
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            elif c == ";" and depth == 0:
                i += 1
                break
            elif c == "#" and depth == 0 and text[i - 1] == "\n":
                break  # (a directive between items)
            i += 1
        chunk = text[start:i]
        head = re.sub(r"/\*.*?\*/|//[^\n]*", "", chunk.split("{", 1)[0], flags=re.S).strip()
        found = DEFINITION.match(head) if "{" in chunk and chunk.rstrip().endswith("}") and "=" not in head else None
        items.append((chunk, found[1] if found else None, bool(found) and re.match(r"\s*static\b", head) is not None))
    return items


def only(text: str, drop: set[str]) -> str:
    """The text without the definitions of the non-static functions in `drop`: directives, declarations, static
    helpers and every other function kept."""
    return "".join(chunk for chunk, name, static in items(text) if name is None or static or name not in drop)


def defined(text: str) -> set[str]:
    """The non-static functions the text defines."""
    return {name for _, name, static in items(text) if name and not static}
