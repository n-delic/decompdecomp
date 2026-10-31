"""Sony's PlayStation 3 SDK (the Cell SDK, "cell"), the user's own licensed copy: the PPU compilers retail games were
built with and the headers and libraries only the SDK has. Nobody may publish them; the free GPL pack
(packaging/ps3-gcc, unpublished) holds Sony's GCC sources but leaves out libgcc, the C runtime and Sony's intrinsics
headers (si2vmx.h, vmx2spu.h, spu2vmx.h, spu_intrinsics.h, spu_mfcio.h) and every SDK header and library: the
compiler run from the user's copy finds those beside it.

The layout this reads (from public documentation of the SDK's build scripts; no SDK was available to check it, so
each assumption is one line below and tests/test_kits.py builds a stand-in folder of exactly this):
- host-win32/ppu/bin/ppu-lv2-gcc.exe: Sony's GCC 4.1.1 for the PPU, a Windows program (wibo off Windows);
  host-linux/ppu/bin/ppu-lv2-gcc in SDKs that ship a Linux host (native);
- host-win32/sn/bin/ps3ppusnc.exe: SN Systems' SNC, when the ProDG tools were installed with the SDK;
- target/ppu/include and target/common/include: the SDK's headers.
The SDK generation is the stamp inside the compiler, which GCC also writes into every program's .comment:
"SDK270" ("4.1.1 (SDK270, $Rev: 3019 $)", from SDK 2.x) or "CELL 4.1.2.7" (the first SDKs). A game is built with the
copy whose stamp equals its own (consoles/ps3/__init__.py default_compiler).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

GCC = ("host-linux/ppu/bin/ppu-lv2-gcc", "host-win32/ppu/bin/ppu-lv2-gcc.exe")
SNC = "host-win32/sn/bin/ps3ppusnc.exe"
INCLUDES = ("target/ppu/include", "target/common/include")
# what Sony's GCC puts in its version string: "(SDK270, $Rev: 3019 $)", "(CELL 4.1.2.7, RTTI, $Rev: 1830 $)"
_STAMP = re.compile(rb"\((SDK\d+|CELL \d+(?:\.\d+)+)[,)]")


def stamp(data: bytes) -> str:
    """The SDK stamp in a compiler's bytes or a program's .comment ("SDK270"), or ""."""
    m = _STAMP.search(data)
    return m.group(1).decode() if m else ""


def _gcc(folder: Path) -> Path | None:
    """The PPU GCC of a copy: the Linux host's on Linux (it runs there), else the Windows one."""
    return next((g for rel in GCC if (rel.endswith(".exe") or sys.platform == "linux") and (g := find(folder, rel))),
                None)


class Ps3Sdk(Kit):
    id = "ps3sdk"
    name = "Sony PlayStation 3 SDK (your own copy)"
    platform = "PS3"
    description = ("The compilers and headers retail PlayStation 3 games were built with. Give the folder of your own "
                   "licensed SDK (the one holding host-win32 and target; usually called cell).")
    files = (GCC[1],)
    places = ("cell", "usr/local/cell", "local/cell")  # (where its installer puts it: C:\usr\local\cell)

    def locate(self, folder: Path) -> Path | None:
        for base in (folder, *(folder / p for p in self.places)):
            try:
                if any(find(base, rel) is not None for rel in (*GCC, SNC)):
                    return base.resolve()
            except OSError:
                continue
        return None

    def build(self, folder: Path) -> str | None:
        """The SDK generation as the compiler stamps it ("SDK270"), read from its driver, else its cc1: from either
        host's compiler, whichever runs here (a copy with only the Linux host's says its build on macOS too)."""
        for gcc in (g for rel in GCC if (g := find(folder, rel)) is not None):
            for exe in (gcc, *sorted(gcc.parent.parent.glob("lib/gcc/ppu-lv2/*/cc1*"))):
                if exe.is_file() and (s := stamp(exe.read_bytes())):
                    return s
        return None

    def verify(self, folder: Path) -> str:
        return f"PlayStation 3 SDK {self.build(folder) or 'of a generation its compiler does not stamp'}"

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        includes = tuple(d for rel in INCLUDES if (d := find(folder, rel, dirs=True)) is not None)
        out = []
        if (gcc := _gcc(folder)) is not None:
            sdk = self.build(folder)
            tag = re.sub(r"[^a-z0-9.]+", "-", sdk.removeprefix("SDK").lower()) if sdk else "unknown"
            out.append(OwnCompiler(f"ps3sdk-{tag}", f"your SDK's PPU GCC ({sdk or 'unstamped'})", gcc, includes,
                                   runner="wibo" if gcc.suffix == ".exe" else "", build=sdk or "",
                                   root=gcc.parent.parent.parent.parent, args=("-c",), flags=("-O2",)))
        if (snc := find(folder, SNC)) is not None:
            built = file_version(snc)
            out.append(OwnCompiler(f"ps3sdk-snc-{built or 'unknown'}", f"your SDK's SNC ({built or 'unversioned'})",
                                   snc, includes, runner="wibo", build="", root=snc.parent.parent.parent.parent,
                                   args=("-c",), flags=("-O2",)))
        return out


PS3SDK = Ps3Sdk()
