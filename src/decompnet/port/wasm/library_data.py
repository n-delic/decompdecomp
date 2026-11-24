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


def function_address_list(code: bytes, vram: int, starts: set[int],
                          gaps: tuple[list[int], list[int]] | None = None) -> list[int]:
    """The addresses of functions (`starts`: the save's function starts) a function's machine code builds with a `lui`
    and the `addiu` or `ori` that finishes it, in the order it builds them: the functions it takes the address of (a
    filter's handler, then its setParam, passed to its constructor).

    A game that maps its code through the TLB builds a function's address at the window's virtual spelling
    (`lui 0x2A; addiu 0xBE20` for 0x002ABE20) while the project names it at the KSEG0 spelling (0x802ABE20): the same
    function, found by its KSEG0 start. `gaps` (the save's function starts and ends, sorted) also admits an address
    in the code image that no function of the save covers: code the analysis took for data (a function that follows
    another's `jr $ra` after a stray word) is still code a constructor names."""
    from capstone import CS_ARCH_MIPS, CS_MODE_BIG_ENDIAN, CS_MODE_MIPS32, Cs
    found, high = [], {}

    def uncovered(address: int) -> bool:
        if not gaps or not gaps[0] or address & 3:
            return False
        first, ends = gaps
        if not first[0] <= address < ends[-1]:
            return False
        at = bisect.bisect_right(first, address) - 1
        return at < 0 or address >= ends[at]
    for insn in Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_BIG_ENDIAN).disasm(code, vram):
        if insn.mnemonic == "lui" and (m := LUI.match(f"lui {insn.op_str}")):
            high[m.group(1)] = (int(m.group(2), 0) & 0xFFFF) << 16
        elif (m := ALU.match(f"{insn.mnemonic} {insn.op_str}")) and m.group(3) in high:
            low = int(m.group(4), 0)
            address = (high[m.group(3)] | (low & 0xFFFF)) if m.group(1) == "ori" else high[m.group(3)] + signed16(low)
            canonical = address | 0x80000000 if 0 < address < 0x80000000 else address
            if canonical in starts or uncovered(canonical):
                if canonical not in found:
                    found.append(canonical)
            if m.group(2) != m.group(3):
                high.pop(m.group(2), None)
    return found


def function_addresses(code: bytes, vram: int, starts: set[int]) -> set[int]:
    """`function_address_list` as a set, for the save's function starts alone."""
    return set(function_address_list(code, vram, starts))


IR_FUNCTION = re.compile(r"^(?:declare|define) [^@\n]*@([\w.$]+)\(", re.M)
IR_TAKEN = re.compile(r"@([\w.$]+)(?![\w.$(])")  # `ptr @name` as a value (not `call @name(`): an address taken


def pair(out: Path, units: set[str], layout: list[dict], names: dict[str, str], rom: bytes, image: int,
         pinned: set[str], entries: set[int] = frozenset(), code_offsets: dict | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """{variable: the game's address} for the variables ultralib's units (`units`: their file names, whose IR is in
    out/ll) define that the game's own library functions share. `layout`: the save's functions (name, vram, size);
    `names`: {the game's function name: its library name}; `rom`/`image`: the cartridge and the address its first
    code is copied to at boot (ROM offset 0x1000); `pinned`: names that already have an address; `entries`: every
    function start the build knows; `code_offsets`: where each function's code is in the project's analysis image
    (play.code_offsets), used instead of the boot copy's layout when the game unpacks its code at run time."""
    functions = {f["name"] for f in layout}
    by_name = {f["name"]: f for f in layout}
    by_library = dict(by_name)  # a unit's library name: the game's function (named so, or by library detection)
    for game, library in names.items():
        if game in by_name:
            by_library.setdefault(library, by_name[game])
    analysed = Path(code_offsets["image"]).read_bytes() if code_offsets and code_offsets.get("image") else None

    def code_of(f: dict) -> bytes | None:
        """A function's machine code: from the analysis image at its own ROM offset when the project has one, else
        from the cartridge's boot copy (ROM offset 0x1000 for `image`)."""
        if not f or not f["size"]:
            return None
        if analysed is not None and (at := (code_offsets["offsets"] or {}).get(f["name"])) is not None:
            return analysed[at:at + f["size"]] if at + f["size"] <= len(analysed) else None
        offset = 0x1000 + f["vram"] - image
        return rom[offset:offset + f["size"]] if 0 < offset and offset + f["size"] <= len(rom) else None
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
    # A variable library code uses that no file defines (the synthesizer's alGlobals when the game's own alInit has
    # no C: the game's matched C and ultralib's units all declare it `external`) still lives at one address in the
    # game, which its unmatched code uses. Left undefined, the compiled C and the game's own machine code kept two
    # copies: the audio frame found no players and made a null command list, which the game's audio thread reports.
    stems = {n for n in by_library if (out / "ll" / (n + ".ll")).is_file()}
    users: dict[str, list[str]] = {}
    for stem in sorted(stems):
        for name in ll_names((out / "ll" / (stem + ".ll")).read_text(encoding="utf-8", errors="replace"), functions)[1]:
            if name not in pinned and not re.fullmatch(r"(?:D|B|g)_[0-9A-Fa-f]+", name):
                users.setdefault(name, []).append(stem)
    if users:
        # a file defines a variable when it spells `@name = global ...`; `= external global` only declares it
        defining = re.compile(rb"^@(" + b"|".join(re.escape(n.encode()) for n in users) + rb") = (?!external )", re.M)
        for path in (out / "ll").glob("*.ll"):
            for m in defining.finditer(path.read_bytes()):
                users.pop(m.group(1).decode(), None)
            if not users:
                break
        for name, stems_using in users.items():  # defined nowhere: the game's address is the one its code uses
            shared[name] = stems_using
            wanted.add(name)
            owned.add(name)
    cache: dict[str, set[int] | None] = {}

    def addresses(library: str) -> set[int] | None:
        if library not in cache:
            f = by_library.get(library)
            code = code_of(f) if f else None
            cache[library] = data_addresses(code, f["vram"], outside) if code else None
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
    # An ultralib function the game's own code has no name for (a mixer's pull routine, large and not recognised by
    # library detection) still has an address: the game's copy of the function that takes its address (the filter's
    # constructor, which passes its handler to alFilterNew) builds it. When one ultralib function the constructor
    # references has no address, and the constructor's machine code builds exactly one function address no other
    # reference accounts for, that is it. Without it the C takes a wasm table index (`2`) and the recompiled code
    # that calls the handler jumps to address 2.
    starts = {f["vram"] for f in layout} | set(entries)  # entries: every function start the build knows (fixed.txt)
    spans = (sorted(f["vram"] for f in layout if f["size"]),  # where the save's code is: starts, and their ends
             [e for _, e in sorted((f["vram"], f["vram"] + f["size"]) for f in layout if f["size"])])
    unit_names = {u[:-2] for u in units}
    learned: dict[str, int] = {}  # a function the constructors name but nothing else does (alFilterNew itself)

    def address_of(name: str) -> int | None:
        if name in functions_at:
            return functions_at[name]
        if name in learned:
            return learned[name]
        return by_library[name]["vram"] if name in by_library else None
    changed = True
    while changed:
        changed = False
        guesses: list[tuple[list[str], set[int]]] = []  # (the unknown functions a constructor references, the addresses left)
        for name in sorted(unit_names):
            f = by_library.get(name)
            path = out / "ll" / (name + ".ll")
            if not (f and f["size"] and path.is_file()):
                continue
            code = code_of(f)
            if not code:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            functions_here = set(IR_FUNCTION.findall(text))
            body = "\n".join(line for line in text.splitlines() if not line.startswith(("declare", "define")))
            # the functions whose address this one takes (a call's address is not built by `lui`/`addiu`)
            referenced = {n for n in IR_TAKEN.findall(body) if n in functions_here and n != name}
            # the unknown functions in the order the unit's C takes them (a filter's handler, then its setParam)
            order = list(dict.fromkeys(n for n in IR_TAKEN.findall(body) if n in referenced and address_of(n) is None))
            unknown = sorted(order)
            if not unknown:
                continue
            built = function_address_list(code, f["vram"], starts, spans)
            built = [a for a in built if a != f["vram"]]
            known = {a for n in referenced if (a := address_of(n)) is not None}
            leftover_list = [a for a in built if a not in known]
            leftover = set(leftover_list)
            if len(leftover) == len(unknown) == 1 or (len(leftover) == len(unknown) and len(order) == len(leftover_list)
                                                      and len(order) > 1):
                # every function the code builds is one the unit references; with several, the order the machine
                # code builds them is the order the C takes them (a1 then a2 of the call to alFilterNew)
                guesses.append((order if len(order) > 1 else unknown, leftover_list if len(order) > 1 else leftover))
        # a function several constructors name (alFilterNew) is the one address all their leftovers share; one only
        # a constructor names (its own handler) is what is left of that constructor's addresses
        candidates: dict[str, set[int]] = {}
        for unknown, leftover in guesses:
            if isinstance(leftover, list):  # several: each name takes the address built at its place in the order
                for n, address in zip(unknown, leftover):
                    candidates[n] = candidates[n] & {address} if n in candidates else {address}
                continue
            for n in unknown:
                candidates[n] = candidates[n] & leftover if n in candidates else set(leftover)
        sure = {n: next(iter(c)) for n, c in candidates.items() if len(c) == 1}
        taken = Counter(sure.values())
        for n, address in sorted(sure.items()):
            if taken[address] == 1 and address not in functions_at.values() and address not in learned.values():
                (functions_at if n in unit_names and n not in pinned else learned)[n] = address
                changed = True
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
    # A function a unit defines for itself (a `static` handler such as a player's voice handler, defined inside the
    # constructor's file) is not a unit of its own, so it never got an address above; the constructor's machine code
    # still names it. Without the game's address the C stores a wasm table index (`1`) as the handler, and the game's
    # own audio frame, which now walks the player list (#3157 made alGlobals the game's), jumped to address 1.
    defined: set[str] = set()
    for unit in sorted(units):
        path = out / "ll" / (unit[:-2] + ".ll")
        if path.is_file():
            defined |= set(re.findall(r"^define [^@\n]*@([\w.$]+)\(", path.read_text(encoding="utf-8", errors="replace"), re.M))
    for n, address in sorted(learned.items()):
        if n in defined and n not in pinned and n not in functions_at and address not in functions_at.values():
            functions_at[n] = address
    taken = Counter(found.values())
    return ({name: address for name, address in found.items() if taken[address] == 1}, functions_at)
