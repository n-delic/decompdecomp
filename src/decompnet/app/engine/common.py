"""What the parts of the engine share: compilers and platforms, the header every ROM project starts with,
and small helpers."""

from __future__ import annotations

import json
import re

from ... import toolchains
from ...network import NetworkError

DEFAULT_TOOLCHAIN = {"KMC": "gcc-2.7.2-kmc", "IDO": "ido-7.1", "EEGCC": "ee-gcc-2.96",
                     "EEGCC-2.9": "ee-gcc-2.9-991111", "EEGCC-2.95.2": "ee-gcc-2.95.2", "EEGCC-2.95.3": "ee-gcc-2.95.3",
                     "EEGCC-3.2": "ee-gcc-3.2", "MWCPS2-2": "mwcps2-2.3.3", "MWCPS2-3": "mwcps2-3.0.1"}
# How ready each platform is for a full decomp you can port and mod; the GUI and the README show it.
# recommended: the most tested path, from import to a project that builds the game (GameCube and Wii: the same kind
# of project as decomp.dev's, linked file by file as it's finished; N64: tested on real games, rebuilt byte for byte).
# beta: works and is tested on homemade games, less so on real ones yet.
PLATFORM_STATUS = {"gc": "recommended", "wii": "recommended", "n64": "recommended", "ps2": "beta", "win32": "beta"}
COMPILERS = {
    "gcc-2.7.2-kmc": "many third-party N64 games",
    "ido-7.1": "most later first-party N64 games",
    "ido-5.3": "early N64 games (1996-97)",
    "ee-gcc-2.96": "PlayStation 2 games (Sony's EE GCC)",
    "ee-gcc-2.9-991111": "launch-era PlayStation 2 games (the first SDK's GCC)",
    "ee-gcc-2.95.2": "PlayStation 2 games built with SN Systems' ProDG",
    "ee-gcc-2.95.3": "later PlayStation 2 games built with SN Systems' ProDG",
    "ee-gcc-3.2": "late PlayStation 2 games (2003-2005)",
    "mwcps2-2.3.3": "PlayStation 2 games built with CodeWarrior (2000-2001, proprietary)",
    "mwcps2-3.0.1": "PlayStation 2 games built with CodeWarrior (2002-2005, proprietary)",
}
SHORT_NAMES = {"gcc-2.7.2-kmc": "KMC GCC 2.7.2", "ido-7.1": "IDO 7.1", "ido-5.3": "IDO 5.3",
               "ee-gcc-2.96": "EE-GCC 2.96",
               "ee-gcc-2.9-991111": "EE-GCC 2.9", "ee-gcc-2.95.2": "EE-GCC 2.95.2 (SN)", "ee-gcc-2.95.3": "EE-GCC 2.95.3 (SN)",
               "ee-gcc-3.2": "EE-GCC 3.2", "mwcps2-2.3.3": "CodeWarrior PS2 2.3.3", "mwcps2-3.0.1": "CodeWarrior PS2 3.0.1"}
# CodeWarrior versions in the `mwcc` pack (GameCube, Wii), and Visual C++ versions (PC), for games started from
# scratch.
MW_VERSIONS = {
    "gc": ["GC/1.0", "GC/1.1", "GC/1.1p1", "GC/1.2.5", "GC/1.2.5n", "GC/1.3", "GC/1.3.2", "GC/1.3.2r", "GC/2.0",
           "GC/2.0p1", "GC/2.5", "GC/2.6", "GC/2.7", "GC/3.0a3", "GC/3.0a3.2", "GC/3.0a3.3", "GC/3.0a3.4", "GC/3.0a5",
           "GC/3.0a5.2"],
    "wii": ["Wii/0x4201_127", "Wii/1.0RC1", "Wii/1.0a", "Wii/1.0", "Wii/1.1", "Wii/1.3", "Wii/1.5", "Wii/1.6", "Wii/1.7"],
    "msvc": ["MSVC/6.0", "MSVC/7.0", "MSVC/7.1"],  # the `msvc` pack (PC)
}

# Tools shown in Setup besides the compilers.
TOOLS = {
    "m2c": "machine-made first drafts (MIPS, PowerPC, ARM)",
    "decomp-permuter": "rewrites near misses thousands of ways in the background, so more of them match",
    "objdiff-cli": "judges forks of public decomps, function by function",
    "ghidra": "drafts for PC, Switch and other platforms m2c can't read (540 MB, needs Java 21+)",
    "mwcc": "CodeWarrior, the compilers of GameCube and Wii games (proprietary)",
    "msvc": "Visual C++ 6.0, 7.0 and 7.1, the compilers of PC games (proprietary)",
}
# Functions this small are handed out in batches (claim_function count): agents match most on the first try.
BATCH_INSNS = 48
PLATFORM_COMPILERS = {"n64": ["gcc-2.7.2-kmc", "ido-7.1", "ido-5.3"],
                      "ps2": ["ee-gcc-2.96", "ee-gcc-2.9-991111", "ee-gcc-2.95.2", "ee-gcc-2.95.3", "ee-gcc-3.2",
                              "mwcps2-2.3.3", "mwcps2-3.0.1"]}
def parse_ranges(text: str) -> list[list[int]]:
    """Address ranges written "0x10BFD0-0x10CD40, 0x1106C0-0x1107C0" as [[start, end], ...] (end not included)."""
    spans = [[int(a, 16), int(b, 16)] for a, b in re.findall(r"(?:0x)?([0-9A-Fa-f]{5,8})\s*-\s*(?:0x)?([0-9A-Fa-f]{5,8})", text)]
    if text.strip() and (not spans or any(a >= b for a, b in spans)):
        raise NetworkError('ranges are start-end addresses: "0x10BFD0-0x10CD40, 0x1106C0-0x1107C0"')
    return spans


def _default_flags(toolchain: str, platform: str, gp: int) -> list[str]:
    """A toolchain's flags for a game: PlayStation 2 games keep globals up to 8 bytes $gp-relative when they use $gp
    (GCC's -G8; CodeWarrior's -sdatathreshold 8)."""
    flags = toolchains.get(toolchain).flags
    if platform != "ps2":
        return flags
    small = ["-sdatathreshold", str(8 if gp else 0)] if toolchain.startswith("mwcps2") else [f"-G{8 if gp else 0}"]
    return [f for f in flags if not f.startswith("-G")] + small


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


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "rom"


def _preview(data: bytes | None, kind: str) -> str:
    if not data:
        return ""
    text = data.split(b"\0", 1)[0]
    if len(text) >= 3 and all(32 <= c < 127 or c in (9, 10) for c in text):
        return json.dumps(text.decode("ascii"))
    return data[:16].hex(" ")
