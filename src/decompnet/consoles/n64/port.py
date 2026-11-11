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
    has it), "native" (the game's copy compiles for the PC as it is: math, the synthesizer), "glue" (console
    plumbing libultraship doesn't have: the port's own glue supplies it, a main loop in place of threads, RSP tasks
    handed to the renderer and the mixer, stubs for the 64DD), or None (the game's own code)."""
    bare = name.lstrip("_")
    if name in _MATH:  # before the C library: libultra's __cosf is its own, not the host's cosf
        return "native"
    if name in LIBULTRASHIP or bare in _LIBC or name in _OS_INTERNAL or re.match(r"(Printf|Litob|Ldtob)$", bare):
        return "replaced"
    if (re.match(r"gu[A-Z]", bare) or name in _MATH or re.match(r"(n_)?al[A-Z]|Mus[A-Z]", bare)
            or name in _SYNTH_INTERNAL):
        return "native"
    if re.match(r"os[A-Z]", bare):
        return "glue"
    return None


# What libultraship's libultra layer defines (src/libultraship/libultra/*.cpp at Kenix3/libultraship a3f1e102, the
# commit Torch builds against): the rest of libultra's API is the game's own C (math) or the port's glue.
LIBULTRASHIP_COMMIT = "a3f1e102e1ed50a3f536fbca2e6e412170e7320b"
LIBULTRASHIP = frozenset({
    "__osCurrentTime", "__osEventStateTab", "__osMaxControllers", "__osMotorAccess", "osAiGetLength",
    "osAiSetNextBuffer", "osCartRomInit", "osContGetReadData", "osContInit", "osContStartReadData",
    "osCreateMesgQueue", "osCreatePiManager", "osCreateViManager", "osEPiStartDma", "osEepromLongRead",
    "osEepromLongWrite", "osEepromProbe", "osEepromRead", "osEepromWrite", "osGetCount", "osGetTime",
    "osInvalDCache", "osInvalICache", "osJamMesg", "osMapTLB", "osMotorInit", "osPiReadIo", "osPiStartDma",
    "osPiWriteIo", "osRecvMesg", "osSendMesg", "osSetEventMesg", "osSetTime", "osSetTimer", "osViBlack",
    "osViGetCurrentFramebuffer", "osViGetNextFramebuffer", "osViSetEvent", "osViSetMode", "osViSetSpecialFeatures",
    "osViSetXScale", "osViSetYScale", "osViSwapBuffer", "osVirtualToPhysical", "osWritebackDCache",
    "osWritebackDCacheAll"})
# libultra's own math, pure computation: the game's copy compiles for the PC (libultraship declares some, defines
# none); the controller CRCs are the same kind of code
_MATH = frozenset({"sins", "coss", "__sinf", "__cosf", "__osContAddressCrc", "__osContDataCrc"})
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


# Torch's `gbi` names the command set its display-list reader decodes; the lighter variants send F3DEX's commands.
_GBI = {"Fast3D": "F3D", "F3D": "F3D", "F3DEX": "F3DEX", "F3DLX": "F3DEX", "F3DLP": "F3DEX", "F3DEX2": "F3DEX2",
        "F3DLX2": "F3DEX2"}
_ABOUT = {"archive": "an archive the game's own code unpacks (Torch can't inflate it), kept whole",
          "audio": "audio: sample banks and sequences the game's audio library reads",
          "data": "data the game reads from the cartridge"}


def torch_files(game: dict, entries: list[dict]) -> dict[str, str]:
    """The game's Torch config (config.yml, keyed by the ROM's SHA-1, as Torch picks the config for a ROM) and its
    assets, one BLOB per resource for now: Torch extracts each to the archive libultraship reads."""
    gbi = next((_GBI[g["name"]] for g in game["graphics"] if g["name"] in _GBI), "F3DEX")
    slug = game["slug"]
    config = (f"# {game['title']}: written by decompdecomp from ROM Explorer's map (docs/PORTING.md, phase 2).\n"
              "# Offsets and sizes only: Torch reads the bytes from your own ROM.\n"
              f"{game['sha1']}:\n  name: {_quote(game['title'])}\n  path: {slug}\n  config:\n    gbi: {gbi}\n"
              f"    sort: OFFSET\n    output:\n      binary: {slug}.o2r\n      headers: include\n      code: src\n")
    lines = [f"# The ROM's data outside its code, {len(entries)} resources. Kinds are ROM Explorer's.", ""]
    for e in entries:
        holds = ", ".join(f"{kind} {size:,}" for kind, size in e["holds"].items())
        lines.append(f"# {_ABOUT[e['group']]}. Holds: {holds}"
                     + (f". Loaded by: {', '.join(e['loaders'])}" if e["loaders"] else ""))
        lines += [f"{e['name']}:", "  type: BLOB", f"  offset: 0x{e['offset']:X}", f"  size: 0x{e['size']:X}",
                  f"  symbol: {e['name']}", ""]
    return {"config.yml": config, f"{slug}/rom.yml": "\n".join(lines)}


def torch_extract(assets, image, out, log=print):
    """Run the pinned Torch on the asset map (`assets`: config.yml and its folders) and the user's own ROM: the
    .o2r archive libultraship reads, written into `out`."""
    import subprocess
    from pathlib import Path

    from ... import limits, toolchains
    from ...hostenv import NO_WINDOW
    toolchains.ensure("torch", log=log)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.o2r"):
        old.unlink()
    argv = [str(toolchains.binary("torch")), "o2r", "-s", str(assets), "-d", str(out), str(image)]
    try:
        done = subprocess.run(limits.capped(argv), cwd=out, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Torch took longer than 15 minutes to extract the assets.") from None
    archives = sorted(out.glob("*.o2r"))
    if done.returncode or not archives:
        said = [line for line in (done.stdout + done.stderr).splitlines()
                if "[error]" in line or "[warning]" in line or "[critical]" in line]
        why = ("Torch found no config for this ROM: the asset map is keyed by the ROM's SHA-1, and this file's "
               "isn't the one the project recorded. Write the asset map again." if not done.returncode else
               f"Torch stopped (exit code {done.returncode}).")
        raise RuntimeError(why + ("\n" + "\n".join(said[-12:]) if said else ""))
    return archives[0]


# A resource in an .o2r is a 0x40-byte header (its type at 4, little-endian, so "OBLB" reads "BLBO") then its body;
# a BLOB's body is its size and its bytes.
_BLOB = b"BLBO"


def torch_resources(archive) -> dict:
    """{resource name: the bytes it holds} for the BLOBs in an .o2r (a ZIP); other types aren't read yet."""
    import struct
    import zipfile
    out = {}
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            data = z.read(info)
            if len(data) >= 0x44 and data[4:8] == _BLOB:
                size = struct.unpack_from("<I", data, 0x40)[0]
                out[info.filename.rsplit("/", 1)[-1]] = data[0x44:0x44 + size]
    return out


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


KIT = PortKit(
    name="libultraship and Torch",
    summary="libultra's API, a renderer for the game's display lists, input and saves (libultraship); the assets "
            "extracted from your own ROM into an archive (Torch).",
    sdk_group=_sdk_group,
    microcode=microcode,
    audio=audio_library,
    asset_map=torch_files,
    boot=0x1000,  # the header and IPL3, which the console's boot ROM runs
    extract=torch_extract,
    resources=torch_resources,
)
