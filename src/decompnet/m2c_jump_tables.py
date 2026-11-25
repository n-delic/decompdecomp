"""A jump through a table whose address m2c can't follow, rewritten to read the table where it jumps.

m2c finds a MIPS switch's table from a read of a `jtbl_` symbol in the same block as the `jr`. A compiler that keeps
the table's address in a register across a loop (`addu $v0, $v0, $t2` with `$t2` set before the loop), or in a stack slot
(`sw $v0, 0x20($sp)` ... `lw $t4, 0x20($sp)`), leaves the `jr` with no read in sight: m2c stops with "Unable to
determine jump table" or "Found multiple candidates", and the function has no draft. On one N64 game these were 2 of
the 406 functions without scored C.

The range check before the jump says which table it is: `sltiu $v0, $v1, 7` bounds an index to seven entries, and
only one table of the function has seven words. Just before the `jr` the module writes the read m2c looks for
(`sll`, `lui`, `addu`, `lw` of that table), from the index the check bounded: still in its register, or reloaded
from the stack slot it was loaded from. The module reads assembly text only.
"""
import re

_JR = re.compile(r"^(?P<head>\s*(?:/\*.*?\*/)?\s*)jr\s+\$(?P<reg>\w+)\s*$")
_SLTIU = re.compile(r"\bsltiu\s+\$\w+,\s*\$(?P<index>\w+),\s*(?P<bound>0x[0-9A-Fa-f]+|\d+)\b")
_LABEL = re.compile(r"^\s*(?:\.\w+|\w+):\s*$|^\s*(?:glabel|dlabel)\b")
_LOAD = re.compile(r"\blw\s+\$(?P<dest>\w+),\s*(?P<offset>-?(?:0x[0-9A-Fa-f]+|\d+))\(\$sp\)")
_WORDS_AFTER = re.compile(r"\.word\b")
LOOK_BACK = 40


def table_sizes(data_asm: str) -> dict[str, int]:
    """{table label: number of `.word` entries} of the jump tables in the supplied data."""
    sizes, current = {}, None
    for line in data_asm.splitlines():
        label = re.match(r"\s*dlabel\s+(jtbl_\w+)", line)
        if label:
            current = label[1]
            sizes[current] = 0
        elif current and _WORDS_AFTER.search(line):
            sizes[current] += 1
        elif line.strip() and not line.strip().startswith(("/*", "#")):
            current = None
    return sizes


def _writes(line: str, register: str) -> bool:
    """Whether an instruction's destination (its first operand) is `register`."""
    found = re.search(r"\b(?!sw\b|sh\b|sb\b|swc1\b|sdc1\b|beq|bne|b\w*z\b|j\w*\b|jr\b)[a-z][\w.]*\s+\$(\w+),", line)
    return bool(found) and found[1] == register


def inline_tables(asm: str, data_asm: str) -> str:
    """`asm` with a table read written before each `jr` that has none in the preceding lines; `asm` itself when no
    jump needs one."""
    sizes = table_sizes(data_asm or "")
    if not sizes:
        return asm
    lines = asm.splitlines()
    out, changed = list(lines), 0
    for number in range(len(lines) - 1, -1, -1):
        jump = _JR.match(lines[number])
        if not jump or jump["reg"] in ("ra", "31"):
            continue
        window = lines[max(0, number - LOOK_BACK):number]
        if any("%lo(jtbl_" in line for line in window[-8:]):
            continue
        guard = None
        for back in range(len(window) - 1, -1, -1):
            if _LABEL.match(window[back]):
                break  # (the check and the jump are one straight run of code)
            if found := _SLTIU.search(window[back]):
                guard = back, found
                break
        if not guard:
            continue
        back, found = guard
        matching = [name for name, size in sizes.items() if size == int(found["bound"], 0)]
        if len(matching) != 1:
            continue
        index, between = found["index"], window[back + 1:]
        if any(_writes(line, index) for line in between):
            # the register was reused: the check loaded it from a stack slot, which is still there
            loads = [m for line in window[:back] if (m := _LOAD.search(line)) and m["dest"] == index]
            if not loads:
                continue
            load = f"lw $at, {loads[-1]['offset']}($sp)"
        else:
            load = f"move $at, ${index}"
        table, register = matching[0], jump["reg"]
        out[number:number] = [load, "sll $at, $at, 2", f"lui ${register}, %hi({table})",
                              f"addu ${register}, ${register}, $at", f"lw ${register}, %lo({table})(${register})"]
        changed += 1
    return "\n".join(out) + ("\n" if asm.endswith("\n") else "") if changed else asm
