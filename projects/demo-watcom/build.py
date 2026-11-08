"""Build the CC0 Demo Quest sources as a Watcom PE for console conformance tests.

Set DECOMP_TEST_WATCOM_C to your own Watcom 10.0a installation. No compiler is
downloaded or copied into the repository. The PE link uses host lld-link on
converted COFF objects because the original 10.0a linker cannot run in wibo
on Apple Silicon. This is a test fixture, not the game's matching linker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from decompnet import toolchains
from decompnet.arch.x86_omf import convert
from decompnet.kits import watcom

HERE = Path(__file__).resolve().parent
GAME = HERE.parent / "demo-n64" / "original"
BUILD = HERE / "build"


def main() -> None:
    own = os.environ.get("DECOMP_TEST_WATCOM_C")
    if not own:
        sys.exit("Set DECOMP_TEST_WATCOM_C to your own Watcom 10.0a installation.")
    root = watcom.WATCOM.locate(Path(own))
    compiler = next((c for c in watcom.WATCOM.compilers(root) if c.build == "10.0a" and "-c-" in c.version), None) if root else None
    if compiler is None:
        sys.exit("The demo needs a verified Watcom 10.0a C compiler body and headers.")
    linker = shutil.which("lld-link")
    if linker is None:
        sys.exit("The demo needs lld-link on PATH (LLVM's free COFF linker).")
    BUILD.mkdir(exist_ok=True)
    for source in (GAME / "game.c", GAME / "game.h", HERE / "watmain.c"):
        shutil.copy2(source, BUILD / source.name)
    env = os.environ | {"WATCOM": str(root), "INCLUDE": str(compiler.includes[0])}
    runner = [] if sys.platform == "win32" else [str(toolchains.binary("wibo"))]
    for stem in ("game", "watmain"):
        subprocess.run([*runner, str(compiler.exe), "-bt=nt", "-mf", "-3r", "-ox", f"-fo={stem}.obj", f"{stem}.c"],
                       cwd=BUILD, env=env, check=True, timeout=120)
        (BUILD / f"{stem}.coff.obj").write_bytes(convert((BUILD / f"{stem}.obj").read_bytes()))
    subprocess.run([linker, "/nologo", "/nodefaultlib", "/entry:start", "/alternatename:_start=start_",
                    "/alternatename:__init_387_emulator=_init_387_emulator_", "/safeseh:no", "/subsystem:console",
                    "/fixed", "/opt:noref", "/out:demoquest.exe", "/map:symbols.map",
                    "game.coff.obj", "watmain.coff.obj"], cwd=BUILD, check=True, timeout=120)
    symbols = {match.group(1): int(match.group(2), 16)
               for match in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8,16})\s+\S+\.coff\.obj$",
                                        (BUILD / "symbols.map").read_text(), re.M)}
    if len(symbols) < 15 or "abs_s32_" not in symbols:
        sys.exit("The Watcom demo link map lacks expected game functions.")
    (BUILD / "demo.json").write_text(json.dumps({"game": "demoquest.exe", "symbols": symbols,
                                                  "toolchain": f"Watcom/{compiler.version}"}, indent=2), encoding="utf-8")
    print(f"Built {BUILD / 'demoquest.exe'} with {compiler.name}; {len(symbols)} symbols")


if __name__ == "__main__":
    main()
