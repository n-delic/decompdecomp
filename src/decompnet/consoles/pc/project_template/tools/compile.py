"""Compile one PC unit and normalize its symbol names, without a shell parsing Windows paths or compiler flags."""

import argparse
import subprocess
import sys
from pathlib import Path

COMPILE_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> int:
    """Keep the compiler's arguments intact, and never rename symbols after a failed compile."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--wrapper", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    command = ([args.wrapper] if args.wrapper else []) + [args.compiler, *flags, "/Iinclude", f"/I{args.include}",
                                                         f"/Fo{args.output}", args.source]
    result = subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable, str(Path(__file__).with_name("names.py")), args.config, args.output],
                          timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode


if __name__ == "__main__":
    sys.exit(main())
