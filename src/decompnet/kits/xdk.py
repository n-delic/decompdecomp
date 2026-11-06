"""Microsoft's Xbox Development Kit, the user's own copy: the libraries every original Xbox game linked (xbox/lib),
which find the XDK's code in a game (consoles/xbox/xdklib.py), and the Visual C++ it shipped (xbox/bin/vc7 or
vc71). From XDK 5233 on that's Visual C++ 7.1 build 3077, the msvc pack's 7.1 file for file; the earlier XDKs
shipped Visual C++ 7.0 builds of their own (13.00.9254 in 3911), which nothing else has: an early game builds with
its XDK's (consoles/xbox/scratch.py game_info).
"""

from __future__ import annotations

import struct
from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

XAPILIB = "lib/xapilib.lib"
VERSION = "xdk"  # the compiler's version in a project's build, with the XDK's build after it (MSVC/xdk-3911)


class Xdk(Kit):
    id = "xdk"
    name = "Microsoft Xbox Development Kit (your own copy)"
    platform = "Xbox"
    description = ("The libraries of original Xbox games, and the compiler of early ones. Give the folder of your own "
                   "copy (the one holding xbox/include and xbox/lib).")
    files = ("include/xtl.h", XAPILIB)
    places = ("xbox", "XDK/xbox", "XDK")  # (the folders its installer and its archives unpack)
    versions = (VERSION,)
    known_files = ("bin/vc71/c2.dll", "bin/vc7/c2.dll")
    known = {  # its code generator, c2.dll: which compiler build the copy has
        "9dbf908b9437dbb42902ffe29ae14c6305bb5ff4f33ff01a95cc9060b7d16a5c": "Visual C++ 13.00.9254.1, XDK 3911's",
        "bcd28f39b1798b07989c30a1df471cf87fc20e3802d130901a08f83800e6b81e":
            "Visual C++ 13.10.3077, XDK 5849's (the msvc pack's 7.1)",
    }

    def build(self, folder: Path) -> str | None:
        """xapilib.lib's library version record, which every game copies into its XBE: the XDK's build."""
        lib = find(folder, XAPILIB)
        data = lib.read_bytes() if lib else b""
        at = data.find(b"XAPILIB\0")
        return str(struct.unpack_from("<4H", data, at + 8)[2]) if at >= 0 and at + 16 <= len(data) else None

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        cl = next((c for sub in ("bin/vc71/cl.exe", "bin/vc7/cl.exe") if (c := find(folder, sub)) is not None), None)
        include = find(folder, "include/xtl.h")
        if cl is None or include is None:
            return []
        xdk = self.build(folder)
        c2 = find(cl.parent, "c2.dll")
        built = file_version(c2) if c2 else ""
        name = f"XDK {xdk}'s Visual C++" if xdk else "your XDK's Visual C++"
        # (one compiler per XDK build: two copies of different XDKs don't collide in a project's build)
        return [OwnCompiler(f"{VERSION}-{xdk}" if xdk else VERSION, f"{name} ({built})" if built else name, cl,
                            (include.parent,), build=built)]


XDK = Xdk()
