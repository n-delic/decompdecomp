"""The N64's port kit: libultraship and Torch (docs/PORTING.md).

libultraship reimplements libultra's API over a PC window, renders the game's display lists with its own copy of
the RSP's graphics commands, and reads assets from an archive that Torch extracts from the user's ROM. What the
readiness report needs from it: which libultra functions it replaces, which graphics microcode it renders, and how
to read the microcode a game loads.
"""

from __future__ import annotations

import re

from .. import PortKit

# Nintendo's graphics microcode signs itself in the ROM: "RSP Gfx ucode F3DEX       fifo 2.05  Yoshitaka Yasumoto
# 1998 Nintendo." Super Mario 64's Fast3D signs as "RSP SW Version: 2.0D, 04-01-96". Audio microcode has no
# signature; the audio library the game links says which commands it sends.
_GFX = re.compile(rb"RSP Gfx ucode ([A-Za-z0-9]+)(?:\.([A-Za-z]+))?\s+(fifo|xbus)?\s*(\d\.\d\d)")
_FAST3D = re.compile(rb"RSP SW Version: (\d\.\d[A-Z]?)")

# Which command sets libultraship's interpreter covers. Variants (.Rej, .NoN, fifo, xbus) send the same commands,
# so the family decides. S2DEX's 2D commands are only partly there (backgrounds and sprites as the community's
# ports needed them), and the lighter F3DLX and F3DLP are F3DEX's commands with less precision, not yet tried:
# the game's own use has to be tried. Families not listed are not in the kit.
_RENDERED = {"Fast3D": "rendered", "F3D": "rendered", "F3DEX": "rendered", "F3DEX2": "rendered",
             "F3DLX": "check", "F3DLX2": "check", "F3DLP": "check", "S2DEX": "check", "S2DEX2": "check"}


def microcode(image: bytes) -> list[dict]:
    """The graphics microcode a ROM carries, each once: [{"name", "variant", "version", "support"}]."""
    found, seen = [], set()
    for m in _GFX.finditer(image):
        name = m.group(1).decode()
        key = (name, m.group(2), m.group(4))
        if key in seen:
            continue
        seen.add(key)
        found.append({"name": name, "variant": (m.group(2) or b"").decode(), "version": m.group(4).decode(),
                      "support": _RENDERED.get(name, "missing")})
    for m in _FAST3D.finditer(image):
        version = m.group(1).decode()
        if ("Fast3D", None, version) not in seen:
            seen.add(("Fast3D", None, version))
            found.append({"name": "Fast3D", "variant": "", "version": version, "support": "rendered"})
    return found


def _sdk_group(name: str) -> str | None:
    """Where a libultra or C library function goes in the port: "replaced" (libultraship or the host's C library
    has it), "native" (the game's copy compiles for the PC as it is), or None (the game's own code)."""
    bare = name.lstrip("_")
    if re.match(r"(os|gu)[A-Z]|(Printf|Litob|Ldtob)$", bare) or bare in _LIBC or name in _OS_INTERNAL:
        return "replaced"
    if re.match(r"(n_)?al[A-Z]|Mus[A-Z]", bare) or name in _SYNTH_INTERNAL:
        return "native"
    return None


_LIBC = frozenset({"bcopy", "bzero", "bcmp", "memcpy", "memset", "memmove", "memcmp", "strlen", "strcpy",
                   "strncpy", "strcmp", "strncmp", "strcat", "strchr", "sprintf", "vsprintf", "sqrtf", "sinf",
                   "cosf", "sins", "coss", "ldiv", "lldiv", "div", "abs", "rand", "srand", "ldexpf", "ldexp",
                   "frexpf", "frexp", "modff", "modf"})
# libultra's own static helpers, named in its source without its prefixes: the VI manager's thread and the
# Controller Pak's file system checks (libultraship does both), and the synthesizer's (compiled with it).
_OS_INTERNAL = frozenset({"viMgrMain", "corrupted", "corrupted_init"})
_SYNTH_INTERNAL = frozenset({"_allocatePVoice", "_freePVoice", "_collectPVoices", "__allocParam", "__freeParam",
                             "_timeToSamples", "_timeToSamplesNoRound", "__nextSampleTime", "_decodeChunk",
                             "_loadOutputBuffer", "_loadBuffer", "_saveBuffer", "_filterBuffer", "_doModFunc",
                             "_getRate", "_getVol", "_pullSubFrame", "_init_lpfilter", "__postNextSeqEvent",
                             "__setInstChanState", "__resetPerfChanState", "__initFromBank", "__initChanState",
                             "__lookupVoice", "__lookupSound", "__mapVoice", "__unmapVoice", "__vsDelta",
                             "__vsVol", "__vsPan", "__seqpReleaseVoice", "__seqpStopOsc", "__voiceNeedsNoteKill"})


def audio_library(names) -> dict:
    """Which audio library a game links, from its functions' names, and what the port needs for it. A family counts
    from two functions: one function's code can equal a library's by chance (a signature, one short body)."""
    names = set(names)
    count = {"n_audio": sum(n.startswith("n_al") for n in names),
             "synth": sum(bool(re.match(r"_*al[A-Z]", n)) for n in names),
             "libmus": sum(n.startswith(("Mus", "__Mus")) for n in names)}
    synth = ("libultra's n_audio synthesizer" if count["n_audio"] >= max(2, count["synth"]) else
             "libultra's audio synthesizer" if count["synth"] >= 2 else "")
    library = " over ".join(x for x in ("libmus (MusPlayer)" if count["libmus"] >= 2 else "", synth) if x)
    if not library:
        return {"library": "", "need": "No known audio library is named yet: name the game's audio functions first, "
                                        "or it plays its own way and needs its own port."}
    return {"library": library,
            "need": f"{library} compiles for the PC as it is; the audio commands it sends to the RSP need a mixer on "
                    "the CPU in the port."}


KIT = PortKit(
    name="libultraship and Torch",
    summary="libultra's API, a renderer for the game's display lists, input and saves (libultraship); the assets "
            "extracted from your own ROM into an archive (Torch).",
    sdk_group=_sdk_group,
    microcode=microcode,
    audio=audio_library,
)
