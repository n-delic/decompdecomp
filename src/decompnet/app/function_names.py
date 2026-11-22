"""Function naming state, separate from whether its C has matched."""

import json
import re
import time

from ..polish_names import generated

# Giving a symbol a compiler builtin's spelling can change calls or reject existing prototypes.
BUILTIN_NAMES = frozenset("bzero bcopy bcmp memcpy memmove memset memcmp memchr strlen strchr strrchr strcpy strncpy strcmp "
                          "strncmp strcat strncat strstr sqrt sqrtf sin sinf cos cosf tan tanf fabs fabsf "
                          "abs labs malloc calloc realloc free printf sprintf snprintf puts putchar exit abort".split())


def check_rename(old: str, new: str):
    """A donor's placeholder must never erase an established descriptive symbol."""
    if old != new and not generated(old) and not old.startswith('func_') and new.startswith('func_'):
        from ..network import NetworkError
        raise NetworkError(f"Keep {old}: {new} is a generated function name, not a naming improvement.")


def needs_name(fn) -> bool:
    return generated(json.loads(fn["meta"] or "{}").get("real") or fn["name"])


def state(fn) -> dict:
    meta = json.loads(fn["meta"] or "{}")
    naming = meta.get("naming")
    return {"named": not generated(meta.get("real") or fn["name"]), "renamed": bool(naming), "naming": naming}


def record(db, fid, old, new, **provenance):
    row = db.execute("SELECT meta FROM functions WHERE id=?", (fid,)).fetchone()
    meta = json.loads(row["meta"] or "{}")
    previous = meta.get("naming", {})
    meta["naming"] = {"original_name": previous.get("original_name", old), "previous_name": old,
                      "name": new, "method": "rename", "at": time.time(), **provenance}
    db.execute("UPDATE functions SET meta=? WHERE id=?", (json.dumps(meta), fid))


# Replace identifier tokens only: a renamed symbol appearing in a string must keep its original bytes.
_C_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*', re.S)


def rename_identifiers(source: str, names: dict[str, str]) -> str:
    return _C_TOKEN.sub(lambda match: names.get(match[0], match[0]), source)


def canonical_source(fn, source: str) -> str:
    """Old saved attempts can predate a rename. Replay the function under its current symbol, preserving literals,
    comments and whitespace (period compilers can schedule differently when formatting changes)."""
    row = dict(fn)
    meta = json.loads(row.get("meta") or "{}")
    naming = meta.get("naming") or {}
    aliases = {naming.get("original_name"), naming.get("previous_name")}
    if row.get("vram") is not None:
        aliases.add(f"func_{fn['vram']:08X}")
    aliases.discard(None)
    aliases.discard(fn["name"])
    if not any(alias in source for alias in aliases):
        return source
    return rename_identifiers(source, dict.fromkeys(aliases, fn["name"]))
