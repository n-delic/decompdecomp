#!/usr/bin/env python3
"""Compile C with any pinned toolchain through the app's own compiler path, and compare it with the target's asm.

Fast enough to think with (about a second), and it answers the question that cost the most time on a stuck function:
*which compiler writes this idiom?* Run it in an environment that has decompnet (`.venv/bin/python`).

    compile_snippet.py TOOLCHAIN FILE.c                       disassemble what the compiler writes
    compile_snippet.py TOOLCHAIN FILE.c --asm target.s        ... and diff it against a function's .s (splat/asm format)
    compile_snippet.py TOOLCHAIN FILE.c -- FLAG FLAG ...      with flags other than the toolchain's defaults

Toolchain ids are the registry's (`ido-7.1`, `ido-5.3`, `gcc-2.7.2-kmc`, `gcc-2.8.1-pm`, `sn-n64-281`, ...). The C gets a
prelude of u8/s8/s16/u16/s32/u32 typedefs unless it has its own. Only compiles; the app judges (judge.py try).
Idioms worth checking this way: a byte load shifted with `sra` (IDO) versus `srl` (every GCC), `or` versus `addu` for
a move. Brute-forcing source shapes under the wrong compiler never finds them.
"""
import difflib
import io
import re
import sys
import tempfile
from pathlib import Path

PRELUDE = ("typedef unsigned char u8;typedef signed char s8;typedef short s16;typedef unsigned short u16;"
           "typedef int s32;typedef unsigned u32;\n")


def canon(line):
    line = re.sub(r"\.L[0-9A-F]+", "L", line)
    line = re.sub(r"\b(?:func|D)_\w+", "F", line)
    line = re.sub(r"\b0x([0-9a-fA-F]+)\b", lambda m: str(int(m[1], 16)), line)
    line = re.sub(r"(?<![\w$])-0x([0-9a-fA-F]+)", lambda m: "-" + str(int(m[1], 16)), line)
    line = re.sub(r"(?<=[ ,])\(\$", "0($", line)
    line = re.sub(r"^or (\$\w+), (\$\w+), \$zero$", r"move \1, \2", line)
    line = re.sub(r"^jal .*", "jal F", line)
    return re.sub(r"^(b\w*) .*,\s*\S+$", lambda m: m[1], line)


def compile_c(toolchain, source, flags=None):
    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet import compiler, project
    config = {"compiler": {"toolchain": toolchain, **({"flags": flags} if flags else {})}}
    cfg = project.compiler_config(Path(tempfile.mkdtemp()), config)
    result = compiler.compile_source(cfg, "", source if "typedef" in source else PRELUDE + source)
    if not result.ok:
        return None, result.log
    text = ELFFile(io.BytesIO(result.obj)).get_section_by_name(".text")
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_BIG_ENDIAN)
    return [(i.mnemonic + " " + i.op_str).strip() for i in md.disasm(text.data(), 0)], ""


def target_lines(path):
    lines = []
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*/\*.*\*/\s+(\S+)\s*(.*)", line)
        if m:
            lines.append((m[1] + " " + m[2].strip()).strip())
    return lines


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    toolchain, path, rest = argv[0], argv[1], argv[2:]
    asm = None
    if "--asm" in rest:
        asm = rest[rest.index("--asm") + 1]
        rest = rest[:rest.index("--asm")] + rest[rest.index("--asm") + 2:]
    flags = rest[rest.index("--") + 1:] if "--" in rest else None
    mine, log = compile_c(toolchain, Path(path).read_text(), flags)
    if mine is None:
        sys.exit(f"does not compile with {toolchain}:\n{log[:1200]}")
    if not asm:
        print("\n".join(mine))
        return
    a, b = [canon(x) for x in target_lines(asm)], [canon(x) for x in mine]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    print(f"{toolchain}: target {len(a)} instructions, mine {len(b)}; {matcher.ratio():.3f} of them equal")
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            print(f"  {tag} @{i1}: target {a[i1:i2][:5]} | mine {b[j1:j2][:5]}")


if __name__ == "__main__":
    main(sys.argv[1:])
