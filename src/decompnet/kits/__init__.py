"""The connector for kits: compilers and SDKs the user brings. A kit is proprietary software decompdecomp can't pin
and download (toolchains.toml), as nobody may publish it: a console's SDK, or a compiler that only ever came in one.
The user points Setup at their own copy; decompdecomp checks it holds what the kit needs, records where it is, and
reads it there. Nothing of it is copied into projects or the toolchains folder, downloaded, or put in CI.

Every kit is a module in this folder (kits/xdk.py: Microsoft's Xbox Development Kit) that makes a `Kit` below and
fills in its parts; the list at the end of this file registers them. A kit belongs to no console: a console says
which kits it takes (Console.kits) and asks this connector for what they give it (`folder`, `compilers`), and the
rest of the app (Setup, toolchains.py) sees a kit as a toolchain of kind "local". Nothing outside this folder
compares kit names, but the consoles that take them (tests/test_kits.py checks it).

What a kit can give:
- its folders (the user can give several copies: two XDKs, a Visual C++ 6.0 and a 2008), where the console reads what it needs from it (the XDK's libraries, which find its code in a game);
- its compilers (`OwnCompiler`): what a project's build runs for a version of its own, with the kit's headers,
  through the runner the program needs (wibo or wine on Linux and macOS);
- its build, read from its files (`build`), and whether those files are ones decompdecomp knows (`verify`: their
  SHA-256 in KNOWN), which Setup shows: a kit isn't pinned as a download is, so this is how a user knows their copy
  is the one a game was built with.

Adding a kit: copy kits/xdk.py, fill in its parts, add it to KITS below, name it in the Console.kits of the consoles
that take it, and pass tests/test_kits.py (CONTRIBUTING.md, "Adding a kit").
"""

from __future__ import annotations

import functools
import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnCompiler:
    """A compiler in the user's kit, as a project's build runs it."""
    version: str  # what a project's build calls it, unique per copy (the compiler id is "MSVC/" + "xdk-3911")
    name: str  # what the app shows: "XDK 3911's Visual C++ (7.0.9254.0)"
    exe: Path
    includes: tuple[Path, ...]  # its headers, searched in this order
    runner: str = "wibo"  # what runs it on Linux and macOS: "wibo", "wine", or "" (a program of this computer)
    build: str = ""  # the compiler's exact build, from its c2.dll's version resource ("13.10.3077.0"); "" if unreadable
    # ROM mode (project.compiler_config runs it as a command): the folder it must read whole inside the sandbox (its
    # libexec, specs and libgcc sit beside bin/), the arguments before the flags ("-c"), and the console's default flags
    root: Path | None = None
    args: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    like: str = ""  # a pinned compiler it runs as (its command, with this copy's folder, `root`, in the pack's place)


class Kit:
    """One kit: its facts and parts. Facts are what Setup shows before the user has one; parts read their copy."""

    id = ""  # what Setup records it under (a toolchain id: "xdk")
    name = ""  # "Microsoft Xbox Development Kit (your own copy)"
    platform = ""  # the console it's for, as Setup shows it: "Xbox"
    license = "proprietary"
    description = ""  # one sentence for Setup: what it gives
    files: tuple[str, ...] = ()  # what its folder holds (paths in it, any case: kits are often Windows folders)
    places: tuple[str, ...] = ()  # folders inside the one the user picked where it may be ("XDK/xbox")
    versions: tuple[str, ...] = ()  # the versions of its compilers (OwnCompiler.version), before a copy is set up
    # SHA-256 of a file of a copy (the first of known_files it has) -> which build of the kit that is
    known_files: tuple[str, ...] = ()
    known: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"<kit {self.id}>"

    def locate(self, folder: Path) -> Path | None:
        """The folder of a copy that `folder` is or holds: it itself, or one of `places` inside it, with all `files`.
        A kit whose copies come in more layouts than that overrides it."""
        for base in (folder, *(folder / p for p in self.places)):
            try:
                if all(find(base, f) is not None for f in self.files):
                    return base.resolve()
            except OSError:
                continue
        return None

    def build(self, folder: Path) -> str | None:
        """Which build of the kit the copy is, read from its files ("3911"), or None."""
        return None

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        """The compilers the copy has (none: a kit of libraries and headers only)."""
        return []

    def verify(self, folder: Path) -> str:
        """Whether the copy's files are ones decompdecomp knows, as a sentence for Setup."""
        found = next((f for rel in self.known_files if (f := find(folder, rel)) is not None), None)
        if found is None:
            return ""
        digest = hashlib.sha256(found.read_bytes()).hexdigest()
        return (f"verified: {self.known[digest]}" if digest in self.known else
                f"a build decompdecomp doesn't know ({found.name} differs from every one it knows): matches may "
                "differ from the game's")

    def toolchain(self) -> dict:
        """The kit as toolchains.toml would write it (toolchains.registry adds it): Setup's card for it."""
        return {"name": self.name, "kind": "local", "platform": self.platform, "license": self.license,
                "source": "your own copy: nothing is downloaded", "description": self.description,
                "local_files": list(self.files), "local_places": list(self.places)}


def find(folder: Path, rel: str, dirs: bool = False) -> Path | None:
    """A file (or with `dirs`, a folder) in a folder by its path, ignoring case."""
    at = folder
    for part in Path(rel).parts:
        if not at.is_dir():
            return None
        at = next((c for c in at.iterdir() if c.name.lower() == part.lower()), None)
        if at is None:
            return None
    return at if at.is_file() or (dirs and at.is_dir()) else None


def file_version(path: Path) -> str:
    """A Windows program's file version ("13.10.3077.0"), from the VS_FIXEDFILEINFO of its version resource; "" when
    it has none (an empty stand-in, a program without one)."""
    try:
        stat = path.stat()  # (its size too: Windows' clock can give a file rewritten at once the same mtime)
        return _file_version(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""


@functools.lru_cache(maxsize=64)
def _file_version(path: str, mtime_ns: int, size: int) -> str:
    data = Path(path).read_bytes()
    at = data.find("VS_VERSION_INFO".encode("utf-16-le"))
    sig = data.find(b"\xbd\x04\xef\xfe", at) if at >= 0 else -1  # (0xFEEF04BD: right after the key, padded)
    if sig < 0 or sig - at > 64 or sig + 16 > len(data):
        return ""
    ms, ls = struct.unpack_from("<II", data, sig + 8)
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def folders(kid: str) -> list[Path]:
    """Where the user's copies of a kit are (Setup records any number), those still there; none: it isn't set up."""
    from .. import toolchains

    try:
        return toolchains.local_folders(kid)
    except toolchains.ToolchainError:
        return []


def folder(kid: str) -> Path | None:
    """The first of the user's copies of a kit, or None."""
    return next(iter(folders(kid)), None)


def compilers(kids) -> list[OwnCompiler]:
    """The compilers of every copy of the kits `kids` (a console's Console.kits) the user set up. Two copies of one
    build give one compiler (the first copy's)."""
    out: dict[str, OwnCompiler] = {}
    for k in kids:
        for at in folders(k):
            for c in get(k).compilers(at):
                out.setdefault(c.version, c)
    return list(out.values())


def own_compiler(cid: str) -> OwnCompiler | None:
    """The compiler of the user's copies that a ROM project's compiler id names (OwnCompiler.version), or None: the
    id is a pinned toolchain's, or a copy that isn't set up here. This is how project.compiler_config, and the
    toolchains registry's get/known/installed, find a kit's compiler beside the pinned ones."""
    return next((c for c in compilers(k.id for k in KITS) if c.version == cid), None)


def _registry() -> list[Kit]:
    from .owncompiler import OWN_COMPILERS
    from .ps3sdk import PS3SDK
    from .visualcpp import VISUALCPP
    from .xdk import XDK

    return [XDK, VISUALCPP, PS3SDK, *OWN_COMPILERS]


KITS: list[Kit] = _registry()
_BY_ID = {k.id: k for k in KITS}


def get(kid: str) -> Kit:
    return _BY_ID[kid]
