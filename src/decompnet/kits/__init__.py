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
- its folder, where the console reads what it needs from it (the XDK's libraries, which find its code in a game);
- its compilers (`OwnCompiler`): what a project's build runs for a version of its own, with the kit's headers,
  through the runner the program needs (wibo or wine on Linux and macOS);
- its build, read from its files (`build`), and whether those files are ones decompdecomp knows (`verify`: their
  SHA-256 in KNOWN), which Setup shows: a kit isn't pinned as a download is, so this is how a user knows their copy
  is the one a game was built with.

Adding a kit: copy kits/xdk.py, fill in its parts, add it to KITS below, name it in the Console.kits of the consoles
that take it, and pass tests/test_kits.py (CONTRIBUTING.md, "Adding a kit").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnCompiler:
    """A compiler in the user's kit, as a project's build runs it."""
    version: str  # what a project's build calls it (the compiler id is the console's: "MSVC/" + "xdk")
    name: str  # what the app shows: "XDK 3911's Visual C++ (13.00.9254.1)"
    exe: Path
    includes: tuple[Path, ...]  # its headers, searched in this order
    runner: str = "wibo"  # what runs it on Linux and macOS: "wibo", "wine", or "" (a program of this computer)


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


def find(folder: Path, rel: str) -> Path | None:
    """A file in a folder by its path, ignoring case."""
    at = folder
    for part in Path(rel).parts:
        if not at.is_dir():
            return None
        at = next((c for c in at.iterdir() if c.name.lower() == part.lower()), None)
        if at is None:
            return None
    return at if at.is_file() else None


def folder(kid: str) -> Path | None:
    """Where the user's copy of a kit is, when it's set up in Setup (and still there); None: it isn't."""
    from .. import toolchains

    try:
        return toolchains.local_folder(kid)
    except toolchains.ToolchainError:
        return None


def compilers(kids) -> list[OwnCompiler]:
    """The compilers of the kits `kids` (a console's Console.kits) the user has set up."""
    return [c for k in kids if (at := folder(k)) is not None for c in get(k).compilers(at)]


def _registry() -> list[Kit]:
    from .xdk import XDK

    return [XDK]


KITS: list[Kit] = _registry()
_BY_ID = {k.id: k for k in KITS}


def get(kid: str) -> Kit:
    return _BY_ID[kid]
