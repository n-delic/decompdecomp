"""python -m decompnet.port.wasm SAVE [--console n64]: build the game in a save repository (port/wasm/build.py).

python -m decompnet.port.wasm export SAVE --kind web|app|window|miyoo|steamdeck|ios --out FOLDER [--ultralib]: make an export
from the save repository alone, as the workflow an export adds to the repository does on GitHub (workflows.py):
what the build took from the project comes from the save's port/wasm/export.json."""
import argparse
import sys
from pathlib import Path

if sys.argv[1:2] == ["export"]:
    from ...network import NetworkError
    from .export import KINDS, export_from_save

    parser = argparse.ArgumentParser(prog="python -m decompnet.port.wasm export",
                                     description="Make an export from a save repository alone (no ROM, no project).")
    parser.add_argument("save", type=Path, help="the game's save repository (holds progress/ and port/wasm/)")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--out", type=Path, required=True, help="the folder to make it in (replaced)")
    parser.add_argument("--ultralib", action="store_true",
                        help="install libultra's source if the save builds library code from it (proprietary)")
    args = parser.parse_args(sys.argv[2:])
    try:
        export_from_save(args.save, args.kind, args.out, ultralib=args.ultralib)
    except NetworkError as error:
        sys.exit(f"error: {error}")
else:
    from .build import build

    parser = argparse.ArgumentParser(prog="python -m decompnet.port.wasm",
                                     description="Build a finished game's C as WebAssembly, then as a PC program.")
    parser.add_argument("save", type=Path, help="the game's save repository (holds progress/ and port/wasm/)")
    parser.add_argument("--console", default="n64")
    args = parser.parse_args()
    build(args.save, console=args.console)
