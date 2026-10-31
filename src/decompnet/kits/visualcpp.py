"""Microsoft Visual C++, the user's own copies: any number of them (a Visual C++ 6.0, a 2008, a 2017...). The compilers
decompdecomp downloads (the msvc packs) are particular builds; a game built with another build (a service pack the
packs don't have, 2012, 2013, 2015's later updates, 2017 and newer) matches only with its own. A copy is the folder
the compiler is in, as the products lay it out:
- Visual C++ 6.0: VC98 (bin/cl.exe, bin/c2.dll, include)
- .NET 2002 to 2013: VC, Vc7 (the same inside)
- 2015 and newer: VC/Tools/MSVC/<version> (bin/Hostx86/x86/cl.exe, ...), which the user may also give as the Visual
  Studio folder, Tools/MSVC or the version's folder.
Its build is its code generator's, c2.dll's file version ("9.0.30729.1"), the number a game's Rich header gives for the
compiler (consoles/pc/scratch.py). 2015 and newer run through wine on Linux and macOS (wine.py, as the msvc2015
pack's), older ones through wibo. Their Windows SDK headers are found beside the copy (Windows Kits) when they are
there; verify says when not.
"""

from __future__ import annotations

from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

WINE_FROM = 19  # the first compiler (c2.dll's file version, cl.exe's: 2015's is 19.0) wibo can't run
SDKS = {  # compilers (c2.dll's major: cl.exe's) -> (Windows Kits folder, the include folders it gives)
    range(19, 99): ("10", ("ucrt", "um", "shared")),  # 2015 and newer
    range(17, 19): ("8.1", ("um", "shared")),  # 2012 and 2013
}
BIN = ("bin", "bin/Hostx86/x86", "bin/Hostx64/x86")  # (a 2017 copy's compiler for 32-bit programs, by host)


def _children(folder: Path | None) -> list[Path]:
    try:
        return sorted((c for c in folder.iterdir() if c.is_dir()), reverse=True) if folder else []
    except OSError:
        return []


def _bin(base: Path) -> Path | None:
    """The folder of a copy's compiler: cl.exe and its code generator c2.dll."""
    for sub in BIN:
        at = find(base, sub, dirs=True)
        if at is not None and find(at, "cl.exe") and find(at, "c2.dll"):
            return at
    return None


class VisualCpp(Kit):
    id = "visualcpp"
    name = "Microsoft Visual C++ (your own copy)"
    platform = "PC"
    description = ("A Visual C++ of your own for games the downloadable ones don't build exactly: any version from 6.0 "
                   "to 2022. Give the folder of its compiler (VC98, VC, or VC/Tools/MSVC/<version>) or of Visual "
                   "Studio; add as many copies as you have.")
    files = ("bin/cl.exe", "bin/c2.dll")

    def locate(self, folder: Path) -> Path | None:
        msvc = [find(folder, p, dirs=True) for p in ("Tools/MSVC", "VC/Tools/MSVC")]
        for base in (folder, *_children(folder), *(c for m in msvc for c in _children(m))):
            if _bin(base) is not None:
                return base.resolve()
        return None

    def build(self, folder: Path) -> str | None:
        """c2.dll's file version, "9.0.30729.1": Visual Studio's version, then the build number."""
        c2 = (b := _bin(folder)) and find(b, "c2.dll")
        return (file_version(c2) or None) if c2 else None

    def sdk(self, folder: Path, build: str) -> list[Path]:
        """The Windows SDK header folders beside the copy that its Visual C++ needs (none to look for: 2010 and
        older bring theirs; none found: the list is empty)."""
        major = int(build.split(".")[0])
        wanted = next((v for r, v in SDKS.items() if major in r), None)
        if wanted is None:
            return []
        kit, subs = wanted
        for up in folder.parents[:6]:
            inc = find(up, f"Windows Kits/{kit}/Include", dirs=True)
            for version in _children(inc):  # (the newest first)
                found = [d for s in subs if (d := find(version, s, dirs=True)) is not None]
                if len(found) == len(subs):
                    return found
        return []

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        build, bin_ = self.build(folder), _bin(folder)
        if build is None or bin_ is None:
            return []
        include = find(folder, "include", dirs=True)
        old_sdk = find(folder, "PlatformSDK/include", dirs=True)  # (.NET 2003 brought one)
        runner = "wine" if int(build.split(".")[0]) >= WINE_FROM else "wibo"
        short = ".".join(build.split(".")[:3])
        return [OwnCompiler(f"own-{short}", f"your Visual C++ {build}", find(bin_, "cl.exe"),
                            tuple(p for p in (include, old_sdk, *self.sdk(folder, build)) if p is not None),
                            runner=runner, build=build)]

    def verify(self, folder: Path) -> str:
        build = self.build(folder)
        if build is None:
            return "its c2.dll has no version resource: decompdecomp can't tell which build it is"
        say = f"Visual C++ {build}"
        major = int(build.split(".")[0])
        if any(major in r for r in SDKS) and not self.sdk(folder, build):
            return (f"{say}; no Windows SDK was found beside it (Windows Kits), so a game's #include <windows.h> "
                    "won't find its headers")
        return say


VISUALCPP = VisualCpp()
