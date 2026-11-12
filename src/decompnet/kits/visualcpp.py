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
- 2015: VC/bin (x86), VC/bin/amd64 or VC/bin/x86_amd64 (x64)
- 2017 and newer: VC/Tools/MSVC/<version> (bin/Hostx86/x86/cl.exe, ...), which the user may also give as the Visual
  Studio folder, Tools/MSVC or the version's folder.
Both x86 and x64 targets are read, including copies with only an x64 compiler.
Its build is its code generator's, c2.dll's file version ("9.0.30729.1"), the number a game's Rich header gives for the
compiler (consoles/pc/scratch.py). 2015 and newer run through wine on Linux and macOS (wine.py, as the msvc2015
pack's), older ones through wibo (unverified for 2.x to 5.0: no copy has been tried). Their Windows SDK headers are found beside the copy (Windows Kits) when they are
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
C2 = ("c2.dll", "c2.exe", "cl.exe")  # (the files that hold the compiler's version, in order)
FIRST = 9  # the first 32-bit cl (Visual C++ 2.0); 8.x is Visual C++ 1.x, 16-bit
BIN = ("bin", "bin/Hostx86/x86", "bin/Hostx64/x86")  # (a 2017 copy's compiler for 32-bit programs, by host)

X64_BIN = ("bin/amd64", "bin/x86_amd64", "bin/Hostx64/x64", "bin/Hostx86/x64")


def _children(folder: Path | None) -> list[Path]:
    try:
        return sorted((c for c in folder.iterdir() if c.is_dir()), reverse=True) if folder else []
    except OSError:
        return []


def _bin(base: Path, folders: tuple[str, ...] = BIN) -> Path | None:
    """The folder of a copy's compiler: cl.exe and its code generator c2.dll."""
    for sub in folders:
        at = find(base, sub, dirs=True)
        if at is not None and find(at, "cl.exe") and (find(at, "c2.dll") or find(at, "c2.exe")):
            return at
    return None


class VisualCpp(Kit):
    id = "visualcpp"
    name = "Microsoft Visual C++ (your own copy)"
    platform = "PC"
    description = ("A Visual C++ of your own for games the downloadable ones don't build exactly: any version from 2.0 "
                   "to 2022. Give the folder of its compiler (MSVC20, MSDEV, VC98, VC, or VC/Tools/MSVC/<version>) or of Visual "
                   "Studio; add as many copies as you have.")
    files = ("bin/cl.exe", "bin/c2.dll")

    def locate(self, folder: Path) -> Path | None:
        # Prefer a folder with an x86 compiler: a 32-bit game needs it, and the newest version (sorted first) may be
        # x64-only (2017+ drops Hostx64/x86 for some toolsets). Fall back to an x64-only copy only if no folder has x86.
        msvc = [find(folder, p, dirs=True) for p in ("Tools/MSVC", "VC/Tools/MSVC")]
        x64_only = None
        for base in (folder, *_children(folder), *(c for m in msvc for c in _children(m))):
            if _bin(base) is not None:
                return base.resolve()
            if x64_only is None and _bin(base, X64_BIN) is not None:
                x64_only = base
        return x64_only.resolve() if x64_only is not None else None

    def build(self, folder: Path) -> str | None:
        """c2.dll's file version, "9.0.30729.1": Visual Studio's version, then the build number."""
        b = _bin(folder) or _bin(folder, X64_BIN)
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
        """Read each target separately: a host's width is not the width of the code it generates.
        Keep the existing x86 ids; x64 copies get the same -x64 suffix as the downloadable compilers."""
        out = []
        for target, folders in (("x86", BIN), ("x64", X64_BIN)):
            bin_ = _bin(folder, folders)
            build = next((v for n in C2 if bin_ and (f := find(bin_, n)) and (v := file_version(f))), None)
            if build is None or int(build.split(".")[0]) < FIRST:
                continue
            include = find(folder, "include", dirs=True)
            old_sdk = find(folder, "PlatformSDK/include", dirs=True)
            # wibo runs 32-bit Windows tools; an x64-hosted code generator needs wine even before VS2015.
            runner = "wine" if target == "x64" or int(build.split(".")[0]) >= WINE_FROM else "wibo"
            short = ".".join(build.split(".")[:3])
            suffix = "-x64" if target == "x64" else ""
            name = f"your Visual C++ {build}" + (" (64-bit)" if suffix else "")
            out.append(OwnCompiler(f"own-{short}{suffix}", name, find(bin_, "cl.exe"),
                                   tuple(p for p in (include, old_sdk, *self.sdk(folder, build)) if p is not None),
                                   runner=runner, build=build))
        return out

    def verify(self, folder: Path) -> str:
        build = self.build(folder)
        if build is None:
            return "its c2.dll has no version resource: decompdecomp can't tell which build it is"
        say = f"Visual C++ {build}"
        major = int(build.split(".")[0])
        if major < FIRST:
            return f"Visual C++ {build} is 16-bit (1.x): decompdecomp builds 32-bit programs only"
        if any(major in r for r in SDKS) and not self.sdk(folder, build):
            return (f"{say}; no Windows SDK was found beside it (Windows Kits), so a game's #include <windows.h> "
                    "won't find its headers")
        return say


VISUALCPP = VisualCpp()
