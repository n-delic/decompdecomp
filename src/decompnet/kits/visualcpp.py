"""Microsoft Visual C++, the user's own copies: any number of them (a Visual C++ 6.0, a 2008, a 2017...). The compilers
decompdecomp downloads (the msvc packs) are particular builds; a game built with another build (a service pack the
packs don't have, 2012, 2013, 2015's later updates, 2017 and newer) matches only with its own. A copy is the folder
the compiler is in, as the products lay it out:
- Visual C++ 2.x: MSVC20 (bin, include); 4.x: MSDEV (bin, include); 5.0: DevStudio/VC (the same inside). Their cl.exe
  is 32-bit and 9.x (2.x), 10.x (4.0 to 4.2) or 11.0 (5.0); 1.5 is 16-bit (cl 8.x) and out of scope. These folder
  layouts and which of cl.exe, c2.dll or c2.exe holds the version are from the products' documentation, not from a
  copy (#137): the code reads c2.dll, then c2.exe, then cl.exe, whichever has a version resource.
- Visual C++ 6.0: VC98 (bin/cl.exe, bin/c2.dll, include)
- .NET 2002 to 2013: VC, Vc7 (the same inside)
- 2015 and newer: VC/Tools/MSVC/<version> (bin/Hostx86/x86/cl.exe, ...), which the user may also give as the Visual
  Studio folder, Tools/MSVC or the version's folder.
Its build is its code generator's, c2.dll's file version ("9.0.30729.1"), the number a game's Rich header gives for the
compiler (consoles/pc/scratch.py). 2015 and newer run through wine on Linux and macOS (wine.py, as the msvc2015
pack's), older ones through wibo (unverified for 2.x to 5.0: no copy has been tried). Their Windows SDK headers are found beside the copy (Windows Kits) when they are
there; verify says when not.
A copy with Visual C++'s 64-bit compiler (2005 on, #278) gives it too, for 64-bit games: the one that runs on a 32-bit
computer and makes 64-bit code (X64_BIN: 2005 to 2015's bin/x86_amd64, 2017 and newer's bin/Hostx86/x64), which wibo
can run as it runs the 32-bit one (2015 and newer: wine). It loads the program database DLLs (mspdb80.dll...) of the
32-bit compiler beside it, so the build also searches that compiler's folder and Visual Studio's Common7/IDE for
DLLs (OwnCompiler.dlls). The 64-bit computer's own (bin/amd64) is a 64-bit program, which wibo can't run: it's used
only through wine, and verify says when a copy has only that one. Its version is the 32-bit one's with "-x64" after
it ("own-14.0.50727-x64"), as the packs' 64-bit compiler's is (consoles/pc X64_SUFFIX).
"""

from __future__ import annotations

from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

WINE_FROM = 19  # the first compiler (c2.dll's file version, cl.exe's: 2015's is 19.0) wibo can't run
SDKS = {  # compilers (c2.dll's major: cl.exe's) -> (Windows Kits folder, the include folders it gives)
    range(19, 99): ("10", ("ucrt", "um", "shared")),  # 2015 and newer
    range(17, 19): ("8.1", ("um", "shared")),  # 2012 and 2013
}
C2 = ("c2.dll", "c2.exe", "cl.exe")  # (the files that hold the compiler's version, in order)
FIRST = 9  # the first 32-bit cl (Visual C++ 2.0); 8.x is Visual C++ 1.x, 16-bit
BIN = ("bin", "bin/Hostx86/x86", "bin/Hostx64/x86")  # (a 2017 copy's compiler for 32-bit programs, by host)
# a copy's compiler for 64-bit programs, its 32-bit host's first (wibo runs only 32-bit programs; the packs' 2017 is
# Hostx86/x64 too); the 64-bit hosts' (bin/amd64, bin/Hostx64/x64) only for a copy wine runs
X64_BIN = ("bin/x86_amd64", "bin/Hostx86/x64")
X64_NATIVE = ("bin/amd64", "bin/Hostx64/x64")
X64 = "-x64"  # after a 64-bit compiler's version (consoles/pc X64_SUFFIX: Compiler.target)
IDE = "Common7/IDE"  # (Visual Studio's folder of shared DLLs, mspdb80.dll's for 2005, beside its VC folder)


def _children(folder: Path | None) -> list[Path]:
    try:
        return sorted((c for c in folder.iterdir() if c.is_dir()), reverse=True) if folder else []
    except OSError:
        return []


def _bin(base: Path, subs: tuple[str, ...] = BIN) -> Path | None:
    """The folder of a copy's compiler (`subs`: BIN, its 32-bit one's; X64_BIN, its 64-bit one's): cl.exe and its
    code generator c2.dll."""
    for sub in subs:
        at = find(base, sub, dirs=True)
        if at is not None and find(at, "cl.exe") and (find(at, "c2.dll") or find(at, "c2.exe")):
            return at
    return None


def _runner(build: str) -> str:
    return "wine" if int(build.split(".")[0]) >= WINE_FROM else "wibo"


def _x64_bin(folder: Path, build: str) -> Path | None:
    """The folder of a copy's compiler for 64-bit programs that its runner can run: the 32-bit host's (X64_BIN), or
    through wine the 64-bit host's too (X64_NATIVE); None: it has none."""
    return _bin(folder, X64_BIN + (X64_NATIVE if _runner(build) == "wine" else ()))


def _ide(folder: Path) -> Path | None:
    """Visual Studio's Common7/IDE beside a copy (its VC folder's parent, or a few folders up for 2017's)."""
    return next((d for up in folder.parents[:6] if (d := find(up, IDE, dirs=True)) is not None), None)


class VisualCpp(Kit):
    id = "visualcpp"
    name = "Microsoft Visual C++ (your own copy)"
    platform = "PC"
    description = ("A Visual C++ of your own for games the downloadable ones don't build exactly: any version from 2.0 "
                   "to 2022. Give the folder of its compiler (MSVC20, MSDEV, VC98, VC, or VC/Tools/MSVC/<version>) or of Visual "
                   "Studio; add as many copies as you have. A copy with its 64-bit compiler (2005 on: the x64 cross "
                   "tools, bin/x86_amd64 or bin/Hostx86/x64) builds 64-bit games of its version too.")
    files = ("bin/cl.exe", "bin/c2.dll")

    def locate(self, folder: Path) -> Path | None:
        msvc = [find(folder, p, dirs=True) for p in ("Tools/MSVC", "VC/Tools/MSVC")]
        for base in (folder, *_children(folder), *(c for m in msvc for c in _children(m))):
            if _bin(base) is not None:
                return base.resolve()
        return None

    def build(self, folder: Path) -> str | None:
        """c2.dll's file version, "9.0.30729.1": Visual Studio's version, then the build number."""
        b = _bin(folder)
        return next((v for n in C2 if b and (f := find(b, n)) and (v := file_version(f))), None)

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
        """Its 32-bit compiler, and its 64-bit one when it has one (#278), with the same headers."""
        build, bin_ = self.build(folder), _bin(folder)
        if build is None or bin_ is None or int(build.split(".")[0]) < FIRST:
            return []
        include = find(folder, "include", dirs=True)
        old_sdk = find(folder, "PlatformSDK/include", dirs=True)  # (.NET 2003 brought one)
        runner = _runner(build)
        short = ".".join(build.split(".")[:3])
        includes = tuple(p for p in (include, old_sdk, *self.sdk(folder, build)) if p is not None)
        out = [OwnCompiler(f"own-{short}", f"your Visual C++ {build}", find(bin_, "cl.exe"), includes, runner=runner,
                           build=build)]
        if (x64 := _x64_bin(folder, build)) is not None:
            # (its own folder first, as Windows looks; then the 32-bit compiler's and Visual Studio's, where a cross
            # compiler's mspdb DLLs are: the packs' 2017 has them copied beside it, toolchains.toml)
            dlls = tuple(dict.fromkeys(d for d in (bin_, _ide(folder)) if d is not None and d != x64))
            out.append(OwnCompiler(f"own-{short}{X64}", f"your Visual C++ {build} (64-bit)", find(x64, "cl.exe"),
                                   includes, runner=runner, build=build, dlls=dlls))
        return out

    def verify(self, folder: Path) -> str:
        build = self.build(folder)
        if build is None:
            return "its c2.dll has no version resource: decompdecomp can't tell which build it is"
        say = f"Visual C++ {build}"
        major = int(build.split(".")[0])
        if major < FIRST:
            return f"Visual C++ {build} is 16-bit (1.x): decompdecomp builds 32-bit programs only"
        if _x64_bin(folder, build) is not None:
            say += " and its 64-bit compiler"
        elif (native := _bin(folder, X64_NATIVE)) is not None:  # (#278: a copy with only the 64-bit computer's)
            say += (f"; its 64-bit compiler ({native.relative_to(folder).as_posix()}) is a 64-bit program, which wibo "
                    "can't run: 64-bit games need the one for 32-bit computers (bin/x86_amd64, Visual Studio's "
                    "\"x64 cross tools\")")
        if any(major in r for r in SDKS) and not self.sdk(folder, build):
            return (f"{say}; no Windows SDK was found beside it (Windows Kits), so a game's #include <windows.h> "
                    "won't find its headers")
        return say


VISUALCPP = VisualCpp()
