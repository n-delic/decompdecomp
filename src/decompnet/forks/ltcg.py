"""Projects for games built with link-time code generation (/GL and /LTCG), such as many original Xbox games.

With /GL the compiler leaves intermediate code in its objects and the linker generates the machine code for the
whole program at once. It gives functions custom register conventions (`this` and arguments in EAX, ESI, EDI or EBX
instead of ECX and the stack, chosen from their callers) and inlines across source files, so a function's bytes
depend on the program around it, and a source file compiled on its own has nothing objdiff can compare.

An LTCG project builds a unit in harness mode (the template's tools/ltcg.py and tools/dll2coff.py): the unit's
source, a driver that calls its functions the way the original's call sites do, and stand-ins for what it uses from
elsewhere, all compiled with /GL and linked into a DLL with /LTCG; its functions are then cut out of the DLL into an
object laid out like the delinked original, and their references are checked for a one-to-one address mapping.
The link is Visual C++'s own linker (the compiler's pack), run through wine on Linux and macOS: wibo can't run it.
This module makes a project one: the template setting, the default flags and a driver skeleton per unit.

An Xbox import tells LTCG from the program (convention.detect: call sites that load EAX, EBX, ESI or EDI right
before calling a function that reads it before writing it and doesn't save it, which no standard convention does),
unless the user says (`create_project(ltcg=...)` or DECOMP_XBOX_LTCG=1/0).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import convention

# The Visual C++ 7.1 flags of an XDK release build with link-time code generation, less /GL (configure.py adds it to
# the units built in harness mode; the rest compile without it): /Ob1 is what a real LTCG game's matches needed, and
# NDEBUG keeps the asserts of release builds out.
XBOX_CL_FLAGS = ["/nologo", "/c", "/O2", "/Ob1", "/G6", "/GF", "/Gy", "/arch:SSE", "/W3", "/D_XBOX", "/DNDEBUG"]

# tools/ltcg.py's ENTRY: a unit is built in harness mode once its driver defines HarnessEntry (not just mentions it)
ENTRY = re.compile(r'^[ \t]*extern[ \t]+"C"[^\n;]*\bHarnessEntry[ \t]*\(', re.M)

DRIVER = """// Harness driver for src/auto/{unit}.cpp. This game was built with link-time code generation (/GL, /LTCG).
//
// Under /LTCG a function's code depends on its callers: the linker passes `this` and arguments in whatever registers
// suit them (EAX, ESI, EDI, EBX...) and inlines across files. So the unit is built with this driver and linked:
// - here, HarnessEntry calls the unit's functions the way the original's call sites do (what each register holds
//   just before the call, which arguments are constants, what the result is used for);
// - harness/{unit}.<name>.cpp hold stand-ins for what the unit calls or reads from the rest of the game, shaped
//   like the real ones (what they read and which registers they preserve); <name>.nogl.cpp compiles without /GL,
//   for middleware or XDK library code the game's link couldn't see into;
// - linker flags for the unit (d3d8ltcg.lib, /d2:-inlT56 ...) go on a line of their own in any of these files:
//   the comment marker, HARNESS-LINK and a colon, then the flags.
// The unit is built this way once HarnessEntry is defined (uncomment it); until then it compiles without /GL.
// Functions are found in the link's map by func_<address> in their names, or by an annotation before the
// definition: `// FUNCTION: 0x<address> [map symbol substring]`.
#include "common.h"

// extern "C" __declspec(dllexport) void HarnessEntry(void)
// {{
// }}
"""


def has_entry(root: Path, unit: str) -> bool:
    """Whether the unit's driver (harness/<unit>.drv.cpp) defines HarnessEntry: the unit is built in harness mode."""
    try:
        return bool(ENTRY.search((root / "harness" / f"{unit}.drv.cpp").read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def prepare(root: Path, groups: dict, library: set[int] = frozenset()) -> None:
    """Make the freshly written project an LTCG one: the template's LTCG setting and a driver skeleton per unit
    (`groups`: unit -> its functions; units of only `library` code, the XDK's, get none)."""
    cfg = root / "tools" / "configure.py"
    text, count = re.subn(r"^LTCG = False$", "LTCG = True", cfg.read_text(encoding="utf-8"), count=1, flags=re.M)
    if not count:
        raise RuntimeError("the template's configure.py has no LTCG setting")
    cfg.write_text(text, encoding="utf-8")
    harness = root / "harness"
    harness.mkdir(exist_ok=True)
    for unit, functions in groups.items():
        if all(f.addr in library for f in functions):
            continue
        path = harness / f"{unit}.drv.cpp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DRIVER.format(unit=unit), encoding="utf-8")


def plan(pe, functions: list, library: set[int], cl_flags: list[str], ltcg: bool | None = None, log=print) -> dict:
    """What an import changes for a game built with link-time code generation, in pc.create's `plan` form (other
    flags, the files before the first build, the manifest's keys). `ltcg`: the user's choice, else $DECOMP_XBOX_LTCG,
    else what the program's call sites say (convention.detect). `library`: the addresses of the XDK's library code,
    which isn't looked at and gets no driver."""
    if ltcg is None and os.environ.get("DECOMP_XBOX_LTCG", ""):
        ltcg = os.environ["DECOMP_XBOX_LTCG"] != "0"
    found = None
    if ltcg is None:
        found = convention.detect_functions(pe, [f for f in functions if f.addr not in library], frozenset(library))
        ltcg = found["ltcg"]
        log(f"link-time code generation? {found['why']}" + "".join(f"\n  e.g. {e}" for e in found["examples"][:2]))
    evidence = {"ltcg_detected": found} if found else {}
    if not ltcg:
        return {"cl_flags": cl_flags, "manifest": evidence}
    log("link-time code generation: units are built in harness mode once their driver (harness/) calls them")
    return {"cl_flags": XBOX_CL_FLAGS, "manifest": {"ltcg": True, **evidence},
            "prepare": lambda root, groups: prepare(root, groups, library)}
