"""A draft written for GCC, made buildable by IDO, for the deep pass's compiler-profile probe.

A profile probe judges the candidate as written under another compiler, and an exact match there changes the
function's settings. IDO is stricter than the GCCs: it refuses arithmetic on a `void *` ("Bad operand type for += or
-="), which m2c writes for every walked byte cursor, so a draft with one never compiled under IDO at all and the probe
could only report "does not compile". A `void *` local that is stepped is a byte cursor (GCC steps it a byte at a
time); `char *` steps the same way under IDO, and every cast through `(char *)` stays what it was. Only the compiler's
refusal is repaired, never a meaning: the probe still needs the judge's exact verdict, and the saved bundle is the
adapted text, which is the one that built.
"""
import re

STEPPED = r'(?:\s*[-+]=|\s*(?:\+\+|--)|(?:\+\+|--)\s*)'
POINTER_LOCAL = re.compile(r'(?m)^([ \t]+)void([ \t]*\*[ \t]*)([A-Za-z_]\w*)[ \t]*;')


def adapted(toolchain: str, source: str) -> str:
    """The source as `toolchain` can build it; the same text for any compiler but IDO, and when nothing needs it."""
    if not toolchain.startswith('ido') or 'void' not in source:
        return source

    def fix(match):
        name = match[3]
        stepped = re.search(rf'(?<![\w.>])(?:{re.escape(name)}{STEPPED}|(?:\+\+|--)\s*{re.escape(name)}\b)', source)
        return f'{match[1]}char{match[2]}{name};' if stepped else match[0]

    return POINTER_LOCAL.sub(fix, source)
