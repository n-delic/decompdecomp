"""libultra's own C for a port, from the user's copy of decompals/ultralib (toolchains.toml `ultralib-source`).

The N64 port compiles libultra's math and synthesizer as the game's C (`PortKit.sdk_group` "native": libultraship
leaves them to the game), so a game's `guTranslate` had to be matched by agents before a port could have it, though
the built-in table (tools/ultralib_signatures.py) already knew it as ultralib's own build. With ultralib installed,
library detection's finding is enough: the function is set aside as SDK code (app/sdk_assembly, the project's
library setting) and the port compiles ultralib's C for it in its place (`units`, port/wasm/build.py `library_c`).

ultralib's C is SGI's and Nintendo's (its files say so), so it's a proprietary pack: installed only when the user
accepts that in Setup, read where it is, and never copied into a save or an export's sources. The WebAssembly build
writes each unit under port/wasm/build/, which git ignores.

A unit is one function: ultralib's file less the file's other non-static functions (`extract`), so a game whose
`guTranslateF` is its own matched C still gets only `guTranslate` from ultralib. It's compiled with ultralib's own
headers and the defines of the build the game's copy matched (`flags`): its version, debug or not, and the ROM build.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .. import consoles, toolchains
from .csource import items as _items

PACK = "ultralib-source"
CORPUS = "built in / decompals/ultralib"  # (sdk_builtin: the ultralib table's corpus starts so)
BUILD = re.compile(r"2\.0([A-L])/(lib(g?)ultra(_d|_rom)?)")
def root() -> Path | None:
    """The installed ultralib, or None: it's installed only from Setup, never fetched in the background."""
    try:
        return toolchains.path(PACK) if toolchains.installed(PACK) else None
    except toolchains.ToolchainError:
        return None


@lru_cache(maxsize=1)
def index(folder: str) -> dict[str, Path]:
    """{function: its C file} of every non-static function ultralib's C defines."""
    out = {}
    for path in sorted(Path(folder, "src").rglob("*.c")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _, name, static in _items(text):
            if name and not static:
                out.setdefault(name, path)
    return out


MACRO = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\([^)]*\)[ \t]+\(?[ \t]*(?:\([^)]*\)[ \t]*)?(\w+)[ \t]*\(", re.M)


@lru_cache(maxsize=1)
def macro_targets(folder: str) -> dict[str, str]:
    """{macro: the ultralib function its expansion calls} from the library's headers: a game's C says `alHeapAlloc(...)`
    and the compiled code calls `alHeapDBAlloc`, a name the game's source text never holds."""
    known = index(folder)
    out = {}
    for path in sorted(Path(folder, "include").rglob("*.h")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for macro, target in MACRO.findall(text):
            if target in known and macro not in known:
                out.setdefault(macro, target)
    return out


def with_macro_targets(names: set, folder: Path) -> set:
    """`names` plus the functions the library's macros among them expand to (`macro_targets`)."""
    return set(names) | {target for macro, target in macro_targets(str(folder)).items() if macro in names}


def prototype(definition: str) -> str:
    """A function definition's own declaration, `f64 _ldexpf(f64 in, s32 ex);`, left where `extract` takes the
    definition out: ultralib's `_getRate` calls `_ldexpf` and `_frexpf` that env.c defines just above it and no
    header declares, so with the definitions gone the unit's calls were implicit `int` ones: `_ldexpf(invn, 30)` took
    and returned an int, the shift count of `_getRate`'s loop came out -1 and the loop (`while (i_invn) i_invn >>= 1`)
    never ended, with the audio thread at the top priority keeping every other thread off the CPU."""
    head = re.sub(r"/\*.*?\*/|//[^\n]*", "", definition.split("{", 1)[0], flags=re.S).strip()
    open_at = head.find("(")
    if open_at < 0:
        return "\n"
    depth = 0
    for at in range(open_at, len(head)):
        depth += (head[at] == "(") - (head[at] == ")")
        if depth == 0:
            break
    else:
        return "\n"
    if head[at + 1:].strip():  # (K&R parameter declarations after the list: only the name and return type)
        head = head[:open_at].rstrip() + "()"
    else:
        head = head[:at + 1]
    return head + ";\n"


def extract(path: Path, name: str) -> str:
    """The file with only `name` of its exported functions: its directives, declarations and static helpers kept."""
    kept, dropped = [], []
    for chunk, defines, static in _items(path.read_text(encoding="utf-8", errors="replace")):
        if defines is None or static or defines == name:
            kept.append(chunk)
        else:
            kept.append(None)
            dropped.append((len(kept) - 1, defines, chunk))
    # a function taken out that the rest still names keeps its declaration in its place (see `prototype`)
    rest = "".join(chunk for chunk in kept if chunk)
    for at, defines, chunk in dropped:
        if re.search(rf"\b{re.escape(defines)}\b", rest):
            kept[at] = "\n" + prototype(chunk)
    kept = [chunk for chunk in kept if chunk]
    # ultralib declares some helpers `static` in a prototype and defines them without it (`_bnkfPatchBank`): the
    # definition is then internal to the file, and the unit made for the function exported nothing, so its callers
    # stayed unresolved. The unit is that function's own file now: its prototype is not static.
    text = re.sub(rf"^([ \t]*)static\s+([^;{{}}]*\b{re.escape(name)}\s*\([^;{{}}]*\)\s*;)", r"\1\2", "".join(kept),
                  flags=re.M)
    return _unstatic_headers(text, path, name)


def _unstatic_headers(text: str, path: Path, name: str) -> str:
    """The same for a prototype in one of the library's own headers beside the file (`#include "sndp.h"` declares
    `static ALMicroTime _sndpVoiceHandler(...)`): that header's text goes in place of the include, with `static`
    dropped from this one prototype, so the function the unit defines is exported (stubbed, it returned 0 and the
    synthesizer's loop over its callbacks never ended). The compiler finds the header's own includes beside the
    file (`flags`: -I the file's folder)."""
    prototype = re.compile(rf"^([ \t]*)static\s+([^;{{}}]*\b{re.escape(name)}\s*\([^;{{}}]*\)\s*;)", re.M)

    def inline(m: re.Match) -> str:
        try:
            header = (path.parent / m.group(1)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return m.group(0)
        patched, changed = prototype.subn(r"\1\2", header)
        return patched if changed else m.group(0)
    return re.sub(r'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', inline, text, flags=re.M)


def flags(folder: Path, path: Path, build: str) -> list[str]:
    """ultralib's own preprocessor flags (makefiles/gcc.mk) for `build` (`2.0I/libgultra_rom`)."""
    m = BUILD.fullmatch(build)
    version, debug, rom = (m[1], m[4] == "_d", m[4] == "_rom") if m else ("L", False, True)
    return ["-nostdinc", "-D_LANGUAGE_C", "-D_MIPS_SZLONG=32", "-D__USE_ISOC99", "-DF3DEX_GBI",
            f"-DBUILD_VERSION=VERSION_{version}", f'-DBUILD_VERSION_STRING="2.0{version}"',
            "-D_DEBUG" if debug else "-DNDEBUG", *(["-D_FINALROM"] if rom else []),
            "-I", str(folder / "include"), "-I", str(folder / "include" / "compiler" / "gcc"),
            "-I", str(folder / "include" / "PR"), "-I", str(path.parent)]


@lru_cache(maxsize=1)
def _builds() -> dict[str, list[str]]:
    """{fingerprint: the builds the ultralib table has it from}."""
    table = consoles.get("n64").sdk_table
    out = {}
    for line in Path(table).read_text(encoding="utf-8").splitlines() if table else []:
        if not line.startswith("#"):
            fingerprint, _, _, builds = line.split("\t")
            out[fingerprint] = builds.split(",")
    return out


def source(name: str, hint: dict, fingerprint: str | None = None) -> tuple[Path, str] | None:
    """(ultralib's file, the build) for a function library detection found as ultralib's own build (`hint`: its
    identity), or None: no ultralib installed, another library's code, or a name ultralib's C doesn't define."""
    folder = root()
    if folder is None or not any(c.startswith(CORPUS) for c in hint.get("corpora") or []):
        return None
    path = index(str(folder)).get(name)
    builds = _builds().get(hint.get("fingerprint") or fingerprint or "", [])
    if path is None or not builds:
        return None
    # the game's build: GCC's libgultra first (the decompdecomp's own N64 games), its ROM build before the debug one
    return path, sorted(builds, key=lambda b: ("gultra" not in b, "_d" in b))[0]


CALLEE = re.compile(r"\b([A-Za-z_]\w*)\b")  # (called or its address taken: a handler is passed as a pointer)


def beside(flag: list[str], path: Path) -> list[str]:
    """`flag` (the flags of another unit's build of the library) for the function in `path`: the same build, with the
    include folder of the file's own directory. `flags` ends with `-I <the file's folder>`, which holds the private
    headers beside it (`synthInternals.h`, `seqp.h`); a callee built with its caller's flags in another folder stopped
    at 'file not found', was left to a stub ("not implemented, skipped"), and the synthesizer's `__freeParam` never
    returned a parameter to its list (72 callees of one audio-heavy game)."""
    if len(flag) >= 2 and flag[-2] == "-I":
        return [*flag[:-1], str(path.parent)]
    return list(flag)


def game_callees(units: dict[str, tuple[str, list[str]]], folder: Path, skip, texts) -> dict[str, tuple[str, list[str]]]:
    """`units` plus the ultralib functions the GAME's own C names (calls or takes the address of) that nothing
    provides (`skip`), built with the flags most of `units` use (the same build of the library). A game's own `alInit`
    called `alSynNew`, which no unit calls and the project had not set aside: nothing defined it, the link stubbed it
    ("not implemented, skipped") and the audio never started. With no unit there is no known build of the library:
    each callee is built with ultralib's default one (the project set nothing aside, and a game's own `alHeapAlloc`,
    which is `alHeapDBAlloc`, then reached a stub and the game's audio setup went on with a null pointer)."""
    known = index(str(folder))
    names = set()
    for text in texts:
        names.update(CALLEE.findall(text))
    names = with_macro_targets(names, folder)
    common = Counter(tuple(flag) for _, flag in units.values()).most_common(1)[0][0] if units else None
    out = dict(units)
    for callee in sorted(names & known.keys()):
        file = f"{callee}.c"
        if file not in out and not skip(callee):
            out[file] = (extract(known[callee], callee),
                         beside(list(common), known[callee]) if common else flags(folder, known[callee], ""))
    return out


def with_callees(units: dict[str, tuple[str, list[str]]], folder: Path, skip) -> dict[str, tuple[str, list[str]]]:
    """`units` plus the ultralib functions they call that nothing else provides (`skip(name)` says what does: the
    game's own C, the host, its recompiled or interpreted functions). A unit is one function, so a synthesizer
    function set aside as ultralib's own called `alFilterNew` and its neighbours, which library detection hadn't
    set aside: nothing defined them, the link stubbed them ("not implemented, skipped") and the audio thread never
    finished a frame, so it kept the CPU from the game's main thread for ever. Each callee is built with the flags of
    the unit that calls it (the same build of the library); what it calls is added in turn."""
    out = dict(units)
    known = index(str(folder))
    pending = list(out.items())
    while pending:
        _, (text, flag) = pending.pop()
        for callee in sorted(with_macro_targets(set(CALLEE.findall(text)), folder)):
            file = f"{callee}.c"
            if callee in known and file not in out and not skip(callee):
                out[file] = (extract(known[callee], callee), beside(flag, known[callee]))
                pending.append((file, out[file]))
    return out


def units(net, slug: str) -> dict[str, tuple[str, list[str]]]:
    """{file name: (C, its flags)} of the functions set aside as SDK code that the port builds from ultralib's C
    (the WebAssembly build's `library_c`). Raises when one needs ultralib and it isn't installed."""
    from ..app.sdk_assembly import ultralib_set_aside
    with net.progress.view():  # (as play.library: reading the project, not syncing its progress)
        proj = net._project(slug)
    wanted = ultralib_set_aside(net, proj)
    if not wanted:
        return {}
    folder = root()
    if folder is None:
        from ..network import NetworkError
        raise NetworkError(f"{len(wanted)} library functions are set aside for ultralib's C (the project's library "
                           "setting): install libultra source (ultralib) in Compilers & tools to build the port")
    return units_from(wanted, folder)


def units_from(wanted: dict, folder: Path) -> dict[str, tuple[str, list[str]]]:
    """units() for {function: (ultralib's file, build)} (a save's port/wasm/export.json keeps them for a runner)."""
    out = {}
    for name, (file, build) in sorted(wanted.items()):
        path = folder / file
        if path.is_file():
            out[f"{name}.c"] = (extract(path, name), flags(folder, path, build))
    return out
