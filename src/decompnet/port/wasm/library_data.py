"""Where the game keeps the state ultralib's C shares (port/wasm).

A library function built from the user's ultralib C (`library_c`) reads and writes ultralib's own variables:
`alGlobals`, the synthesizer's one pointer. The game's other library code (the functions the save has its own C
for, recompiled or interpreted code) uses the game's copy of that variable, at an address of its own
(`D_80034DD0`). The build kept the two apart, so the game's `alInit` set its copy and ultralib's `alAudioFrame`
read its own, still 0: the audio frame never ended, and the audio thread (the higher priority) kept the CPU from
the game's main thread for ever.

The game's copy is found from what the game's own library functions do. A variable ultralib's units define is used
by several functions; the game's address for it is the data address those functions' own machine code all use.
`pair` returns {name: address} for the names it is sure of: one address that every function with code uses (or
three quarters of them, when there are many), with no other address as often. The build pins those names at those
addresses (fixed.txt), as it does a symbol the project names.
"""
from __future__ import annotations

import bisect
import math
import re
from collections import Counter
from pathlib import Path

RAM = range(0x80000000, 0x80800000)  # the console's RAM (4 MB, or 8 MB with the Expansion Pak), cached
LL_GLOBAL = re.compile(r"^@([\w.$]+) = ((?:[a-z_]+ )*?)(global|constant) ", re.M)
LUI = re.compile(r"lui\s+\$(\w+),\s*(-?(?:0x[0-9a-f]+|\d+))$")
ALU = re.compile(r"(addiu|addi|ori)\s+\$(\w+),\s*\$(\w+),\s*(-?(?:0x[0-9a-f]+|\d+))$")
MEM = re.compile(r"(?:l[bhwd]u?|s[bhwd]|lwc1|swc1|ldc1|sdc1|ll|sc)\s+\$(\w+),\s*(-?(?:0x[0-9a-f]+|\d+))\(\$(\w+)\)$")


def signed16(value: int) -> int:
    return ((value + 0x8000) & 0xFFFF) - 0x8000


def ll_names(text: str, functions: set[str]) -> tuple[set[str], set[str]]:
    """(defined, referenced) variables of a unit's IR that other code could share: the module's own non-static,
    non-constant definitions, and the variables it declares `external`. Strings, static data and functions are not."""
    defined, referenced = set(), set()
    for m in LL_GLOBAL.finditer(text):
        name, words, kind = m.group(1), m.group(2).split(), m.group(3)
        if name.startswith(".") or ".str" in name or name in functions:
            continue
        if "external" in words:
            referenced.add(name)
        elif kind == "global" and not ({"internal", "private"} & set(words)):
            defined.add(name)
    return defined, referenced


def data_addresses(code: bytes, vram: int, outside: tuple[list[int], list[int]]) -> set[int]:
    """The RAM addresses a function's machine code builds (a `lui` and the `addiu`, `ori` or load or store that
    finishes it), leaving out addresses inside functions: what is left is data (variables, constants, strings)."""
    from capstone import CS_ARCH_MIPS, CS_MODE_BIG_ENDIAN, CS_MODE_MIPS32, Cs
    starts, ends = outside
    found, high = set(), {}
    for insn in Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_BIG_ENDIAN).disasm(code, vram):
        text = insn.op_str
        if insn.mnemonic == "lui" and (m := LUI.match(f"lui {text}")):
            high[m.group(1)] = (int(m.group(2), 0) & 0xFFFF) << 16
            continue
        address = None
        if (m := ALU.match(f"{insn.mnemonic} {text}")) and m.group(3) in high:
            low = int(m.group(4), 0)
            address = (high[m.group(3)] | (low & 0xFFFF)) if m.group(1) == "ori" else high[m.group(3)] + signed16(low)
            if m.group(2) != m.group(3):
                high.pop(m.group(2), None)
        elif (m := MEM.match(f"{insn.mnemonic} {text}")) and m.group(3) in high:
            address = high[m.group(3)] + signed16(int(m.group(2), 0))
        if address is not None and address in RAM:
            at = bisect.bisect_right(starts, address) - 1
            if not (at >= 0 and address < ends[at]):
                found.add(address)
    return found


def pair(out: Path, units: set[str], layout: list[dict], names: dict[str, str], rom: bytes, image: int,
         pinned: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    """{variable: the game's address} for the variables ultralib's units (`units`: their file names, whose IR is in
    out/ll) define that the game's own library functions share. `layout`: the save's functions (name, vram, size);
    `names`: {the game's function name: its library name}; `rom`/`image`: the cartridge and the address its first
    code is copied to at boot (ROM offset 0x1000); `pinned`: names that already have an address."""
    functions = {f["name"] for f in layout}
    by_name = {f["name"]: f for f in layout}
    by_library = dict(by_name)  # a unit's library name: the game's function (named so, or by library detection)
    for game, library in names.items():
        if game in by_name:
            by_library.setdefault(library, by_name[game])
    ranges = sorted((f["vram"], f["vram"] + f["size"]) for f in layout)
    outside = ([a for a, _ in ranges], [b for _, b in ranges])
    shared: dict[str, list[str]] = {}
    for unit in sorted(units):
        path = out / "ll" / (unit[:-2] + ".ll")
        if not path.is_file():
            continue
        defined, referenced = ll_names(path.read_text(encoding="utf-8", errors="replace"), functions)
        for name in defined | referenced:
            shared.setdefault(name, []).append(unit[:-2])
    wanted = {n for n, us in shared.items() if n not in pinned and not re.fullmatch(r"(?:D|B|g)_[0-9A-Fa-f]+", n)}
    # only names a unit itself defines are ultralib's own state; a name no unit defines is the game's or the host's
    owned = set()
    for unit in units:
        path = out / "ll" / (unit[:-2] + ".ll")
        if path.is_file():
            owned |= ll_names(path.read_text(encoding="utf-8", errors="replace"), functions)[0]
    cache: dict[str, set[int] | None] = {}

    def addresses(library: str) -> set[int] | None:
        if library not in cache:
            f = by_library.get(library)
            offset = 0x1000 + f["vram"] - image if f else -1
            cache[library] = (data_addresses(rom[offset:offset + f["size"]], f["vram"], outside)
                              if f and f["size"] and 0 < offset and offset + f["size"] <= len(rom) else None)
        return cache[library]

    found: dict[str, int] = {}
    functions_at = {}  # an ultralib function's own address in the game: what its address is when the game's C takes it
    for unit in sorted(units):
        name = unit[:-2]
        f = by_library.get(name)
        path = out / "ll" / (name + ".ll")
        if (f and name not in pinned and path.is_file()
                and re.search(rf"^define [^@\n]*@{re.escape(name)}\(", path.read_text(encoding="utf-8", errors="replace"), re.M)):
            functions_at[name] = f["vram"]
    for name in sorted(wanted & owned):
        sets = [a for u in shared[name] if (a := addresses(u))]
        if not sets:
            continue
        counts = Counter(address for a in sets for address in a)
        best = counts.most_common(2)
        address, count = best[0]
        if len(best) > 1 and best[1][1] == count:
            continue  # two addresses as likely: not sure
        if count < (len(sets) if len(sets) <= 3 else math.ceil(0.75 * len(sets))):
            continue
        if len(sets) == 1 and len(sets[0]) != 1:
            continue  # one function and several data addresses: which is which isn't known
        found[name] = address
    taken = Counter(found.values())
    return ({name: address for name, address in found.items() if taken[address] == 1}, functions_at)
