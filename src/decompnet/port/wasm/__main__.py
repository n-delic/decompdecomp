"""python -m decompnet.port.wasm SAVE [--console n64]: build the game in a save repository (port/wasm/build.py)."""
import argparse
from pathlib import Path

from .build import build

parser = argparse.ArgumentParser(prog="python -m decompnet.port.wasm",
                                 description="Build a finished game's C as WebAssembly, then as a PC program.")
parser.add_argument("save", type=Path, help="the game's save repository (holds progress/ and port/wasm/)")
parser.add_argument("--console", default="n64")
args = parser.parse_args()
build(args.save, console=args.console)
