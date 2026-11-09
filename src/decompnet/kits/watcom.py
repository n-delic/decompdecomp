"""#81: the user's Watcom Windows loaders and their original compiler bodies, read in place.

10.0a keeps its bodies in BINB, 10.5 in BINW. The identical small BINNT loaders don't identify a compiler build:
the bodies' SHA-256 does. Unknown bodies remain usable with an explicit unknown-build label; incomplete bodies
under 128 KiB aren't offered (a 64 KiB 10.5 C body failed in wibo and DOSBox). No compiler is run by discovery.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path

from . import Kit, OwnCompiler, find

KNOWN = {
    "c3666de94f6fa6800f452dae8acf45505ecdb62f0ade2cc27cc86c2d9e8e2b6b": "10.0a",
    "647f4e754cd27fc76b3c44a47227ae3baa4ff2e2f253a8ec27216414653b1e6f": "10.0a",
    "362898453ab0fee4260bb8457c5498fb69043958377e18a0046ed72552f80a15": "10.5",
}
MIN_BODY_BYTES = 128 * 1024


@functools.lru_cache(maxsize=64)
def _digest(path: str, size: int, mtime_ns: int) -> str:
    """Setup and compiler lists poll often; a compiler body only needs hashing when its file changes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Watcom(Kit):
    id = "watcom"
    name = "Watcom C/C++ (your own copy)"
    platform = "PC"
    description = ("Your Watcom installation for 32-bit Windows PE and flat DOS LE games. Give its root or a mounted CD's WATCOM "
                   "folder; nothing is copied. C and C++ are separate choices. Other DOS formats are not supported yet.")
    files = ("BINNT/WCC386.EXE",)
    places = ("WATCOM",)
    known = KNOWN
    known_files = ("BINW/WCC386.EXE", "BINB/WCC386.EXE",
                   "BINW/WPP386.EXE", "BINB/WPP386.EXE")

    def locate(self, folder: Path) -> Path | None:
        """A mounted CD or an installed root, ignoring the case of the WATCOM directory too."""
        try:
            for root in (folder, find(folder, "watcom", dirs=True)):
                if root and find(root, "binnt/wcc386.exe") and find(root, "h", dirs=True) and self.compilers(root):
                    return root.resolve()
        except OSError:
            return None
        return None

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        """Each language and body build gets a stable id, never the filename's claimed version."""
        out = []
        try:
            include = find(folder, "h", dirs=True)
        except OSError:
            return []
        for language, name in (("c", "wcc386.exe"), ("cpp", "wpp386.exe")):
            try:
                exe = find(folder, f"binnt/{name}")
                body = next((p for sub in ("binw", "binb") if (p := find(folder, f"{sub}/{name}"))), None)
            except OSError:
                continue
            if exe is None or body is None or include is None:
                continue
            try:
                stat = body.stat()
                if not MIN_BODY_BYTES <= stat.st_size <= 64 * 1024 * 1024:
                    continue
                digest = _digest(str(body), stat.st_size, stat.st_mtime_ns)
            except OSError:
                continue  # a mounted compiler copy may be ejected while Setup polls
            version = KNOWN.get(digest, "unknown")
            out.append(OwnCompiler(f"watcom-{version}-{language}-{digest[:12]}",
                                   f"Watcom {version if version != 'unknown' else 'unknown build'} {'C' if language == 'c' else 'C++'} ({digest[:8]})",
                                   exe, (include,), build=version, root=folder.resolve(),
                                   flags=("-bt=nt", "-mf", "-3r", "-ox"), family="Watcom"))
        return out

    def build(self, folder: Path) -> str | None:
        return ", ".join(dict.fromkeys(c.build for c in self.compilers(folder))) or None

    def verify(self, folder: Path) -> str:
        compilers = self.compilers(folder)
        if not compilers:
            return "No usable compiler body and headers: check BINB/BINW and H, or reinstall your own copy."
        return (super().verify(folder) + "; " + "; ".join(c.name for c in compilers)
                + "; Windows PE / flat DOS LE; use your own licensed Watcom 10.0a/10.5 installation or media, "
                  "and choose the game's exact compiler build.")


WATCOM = Watcom()
