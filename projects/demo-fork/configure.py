"""Check the ROM, extract the targets, write build.ninja and objdiff.json."""
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

from tools.compile import compile_unit

from decompnet import toolchains

ROOT = Path(__file__).resolve().parent
SHA1 = json.loads((ROOT / ".decomp.wtf.json").read_text())["game"]["sha1"]
UNITS = ["game"]


def main() -> int:
    rom = ROOT / "baserom.z64"
    if not rom.exists() or hashlib.sha1(rom.read_bytes()).hexdigest() != SHA1:
        sys.exit("baserom.z64 is missing or isn't Demo Quest 64")
    tc = toolchains.get("gcc-2.7.2-kmc")
    (ROOT / "build" / "expected").mkdir(parents=True, exist_ok=True)
    for unit in UNITS:  # "extract" the targets
        compile_unit(ROOT / "tools" / "reference" / f"{unit}.c", ROOT / "build" / "expected" / f"{unit}.o")
    quote = subprocess.list2cmdline if sys.platform == "win32" else shlex.join
    command = quote([sys.executable, "tools/compile.py"]).replace("$", "$$")
    ninja = ["rule cc", f"  command = {command} $in $out", "  description = cc $in", ""]
    for unit in UNITS:
        ninja.append(f"build build/src/{unit}.o: cc src/{unit}.c | include/game.h")
    ninja.append("")
    (ROOT / "build.ninja").write_text("\n".join(ninja))
    (ROOT / "objdiff.json").write_text(json.dumps({
        "custom_make": "ninja",
        "build_base": True,
        "units": [{"name": unit, "target_path": f"build/expected/{unit}.o", "base_path": f"build/src/{unit}.o",
                   "metadata": {"source_path": f"src/{unit}.c", "complete": False},
                   "scratch": {"platform": "n64", "compiler": "gcc2.7.2kmc", "c_flags": " ".join(tc.flags)}}
                  for unit in UNITS],
    }, indent=2))
    print("configured: run ninja")
    return 0


if __name__ == "__main__":
    sys.exit(main())
