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


def extract(path: Path, name: str) -> str:
    """The file with only `name` of its exported functions: its directives, declarations and static helpers kept."""
    kept = []
    for chunk, defines, static in _items(path.read_text(encoding="utf-8", errors="replace")):
        if defines is None or static or defines == name:
            kept.append(chunk)
    return "".join(kept)


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
