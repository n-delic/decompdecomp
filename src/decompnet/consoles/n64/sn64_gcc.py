"""A `gcc` driver for the reconstructed SN64 GCC 2.7.2 (970404) on a Mac.

A project that downloads SN Systems' GCC 2.7.2 for Linux (tools/modern-sn64/gcc, with its cc1, cpp and a
modern-asn64 `as` beside it) calls `gcc <flags> file.c -c -o file.o`. Those programs are 32-bit Linux files; the
installed `sn-n64-272-970404` has the compiler proper (cc1) and the preprocessor (cpp) built natively, but no driver.
This script is the driver: it preprocesses, compiles and assembles with the project's own `as` (modern-asn64), the
same three steps the original driver ran. Standalone: the wrapper decompdecomp writes in the project runs it with
`python sn64_gcc.py <installed sn folder> <project's as script> <gcc arguments>`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def split(args: list[str]) -> tuple[str | None, str | None, list[str], list[str], list[str]]:
    """(source, output, preprocessor flags, compiler flags, assembler flags) of one gcc command line."""
    source = output = None
    cpp: list[str] = []
    cc1: list[str] = []
    asm: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-o" and i + 1 < len(args):
            output = args[i + 1]
            i += 1
        elif arg in ("-I", "-D", "-U") and i + 1 < len(args):
            cpp += [arg + args[i + 1]]
            i += 1
        elif arg.startswith(("-I", "-D", "-U")):
            cpp.append(arg)
        elif arg.startswith("-Wa,"):
            asm += [a for a in arg[4:].split(",") if a]
        elif arg in ("-c", "-pipe", "-v", "-S"):
            pass
        elif arg.startswith("-"):
            cc1.append(arg)
        else:
            source = arg
        i += 1
    return source, output, cpp, cc1, asm


def main(argv: list[str]) -> int:
    folder, assembler, args = Path(argv[0]), argv[1], argv[2:]
    source, output, cpp, cc1, asm = split(args)
    if not source or not output:
        print("sn64 gcc driver: needs a source file and -o <object>", file=sys.stderr)
        return 1
    # the assembler is told the architecture by the compiler's own -mips flags, not by the project's -Wa list alone
    includes = [a for a in cpp if a.startswith("-I")]
    with tempfile.TemporaryDirectory(prefix="decomp-sn64-") as tmp:
        pre, asmfile = str(Path(tmp, "a.i")), str(Path(tmp, "a.s"))
        steps = [
            [str(folder / "cpp"), "-P", "-undef", "-nostdinc", "-D_LANGUAGE_C", "-D__GNUC__=2", *cpp, source, "-o", pre],
            [str(folder / "cc1"), "-quiet", *cc1, pre, "-o", asmfile],
            [assembler, *asm, *includes, "-o", output, asmfile],
        ]
        for step in steps:
            code = subprocess.run(step).returncode
            if code:
                return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
