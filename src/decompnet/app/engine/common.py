"""What the parts of the engine share: the tools Setup shows, the header every ROM project starts with, and small
helpers. Everything about a console is its package's (consoles/)."""

from __future__ import annotations

import json
import re

from ... import consoles
from ...network import NetworkError

# Tools shown in Setup besides the compilers, with what each console adds (its packs: consoles/<id>/)
TOOLS = {
    "m2c": "machine-made first drafts (MIPS, PowerPC, ARM)",
    "decomp-permuter": "rewrites near misses thousands of ways in the background, so more of them match",
    "objdiff-cli": "judges forks of public decomps, function by function",
} | {tool: use for console in consoles.CONSOLES for tool, use in console.packs}
# ROM mode's compilers, with which games each built, for Setup
COMPILERS = {c.id: c.built for console in consoles.CONSOLES if console.mode == consoles.ROM for c in console.compilers}
# Functions this small are handed out in batches (claim_function count): agents match most on the first try.
BATCH_INSNS = 48


def parse_ranges(text: str) -> list[list[int]]:
    """Address ranges written "0x10BFD0-0x10CD40, 0x1106C0-0x1107C0" as [[start, end], ...] (end not included)."""
    spans = [[int(a, 16), int(b, 16)] for a, b in re.findall(r"(?:0x)?([0-9A-Fa-f]{5,8})\s*-\s*(?:0x)?([0-9A-Fa-f]{5,8})", text)]
    if text.strip() and (not spans or any(a >= b for a, b in spans)):
        raise NetworkError('ranges are start-end addresses: "0x10BFD0-0x10CD40, 0x1106C0-0x1107C0"')
    return spans


_AUTO_TOKEN = re.compile(r"\b(?:func|D|B|jtbl|RO|STR|FLT|DBL)_[0-9A-F]{8}\b")
_ADDRESS = re.compile(r"^(?:0x)?([0-9A-Fa-f]{8})$")

GP_NOTE = """
/* This game keeps small globals (8 bytes or less) in $gp-relative small data. Declare those here WITHOUT
 * `extern` (for example `s32 D_8001B278;`): then the compiler reads them through $gp, like the game does.
 * With `extern`, it builds the full address instead and the function can't match. */
"""

BASE_HEADER = """/* context.h for {title}: shared by every function. Agents grow it with edit_context:
 * add a prototype or struct the moment you work one out, so the next function benefits. */

typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
typedef volatile u8 vu8;
typedef volatile u16 vu16;
typedef volatile u32 vu32;
typedef volatile s32 vs32;
typedef int s32_bool;

#define NULL ((void *)0)
#define TRUE 1
#define FALSE 0
#define ARRAY_COUNT(arr) (s32)(sizeof(arr) / sizeof(arr[0]))
"""


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "rom"


def data_preview(data: bytes | None, kind: str) -> str:
    if not data:
        return ""
    text = data.split(b"\0", 1)[0]
    if len(text) >= 3 and all(32 <= c < 127 or c in (9, 10) for c in text):
        return json.dumps(text.decode("ascii"))
    return data[:16].hex(" ")
