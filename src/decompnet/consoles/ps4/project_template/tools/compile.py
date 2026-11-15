"""Compile one PS4 unit with the pinned clang, without a shell parsing paths or flags."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

COMPILE_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    command = [args.compiler, *flags, f"-I{args.include}", "-I.", "-c", args.source, "-o", args.output]
    code = subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode
    if code == 0:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        command = [sys.executable, str(Path(__file__).with_name("bind.py")), args.output, "--quiet"]
        if (guard := config.get("stack_guard_got")) is not None:
            command += ["--guard", str(int(guard, 0) if isinstance(guard, str) else int(guard))]
        code = subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode
    return code


if __name__ == "__main__":
    sys.exit(main())
