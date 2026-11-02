#!/usr/bin/env python3
"""Cut a unit's functions out of its LTCG harness DLL into the object objdiff compares with the original's
(tools/ltcg.py says why a game built with /GL and /LTCG needs that), and check that the code refers to the same
things the original refers to.

    python tools/dll2coff.py config/<version>/functions.json UNIT DLL MAP OUT.obj SOURCE...

Which functions: the unit's (config/<version>/functions.json). Each is found in the linker's map by name, as the
annotations in the sources say (`// FUNCTION: 0x<address> [map symbol substring]` before a definition, or
`// TARGET 0x<address> <map symbol substring>` anywhere), else by a func_<address> in its name.

The object is laid out the way tools/delink.py lays out the original's functions, so the two sides follow the same
rules: one section per function, from its first byte to where the next symbol starts, less the linker's int3
padding (MSVC puts a switch's jump table after the function's code, inside it); DIR32 relocations where the DLL's
base relocation table has them (an address inside the function, a jump table and its entries, becomes a label);
REL32 for every call or jump with a 32-bit offset that leaves the function. Functions are named with the original's
symbols, references by the map (func_<address> and D_<address> names as tools/names.py gives them).

objdiff, as decompdecomp runs it, doesn't compare what relocations point at. So this also writes OUT.ltcg.json, an
address mapping check per function: relocation by relocation, the original's target address and ours are paired,
and the pairs must form a one-to-one mapping. A function whose code reads one global where the original reads two
(or swaps two callees used twice each) is 100% in objdiff but fails here, and isn't a match.
It also checks what each reference stands for (`data` in OUT.ltcg.json): a D_<address> or func_<address> name, or a
global annotated `// GLOBAL: 0x<address> [map symbol]` before its declaration, says which original address it means,
and must be the address the original refers to there; floats and strings the compiler made are compared by value.
That catches two globals used once each and swapped, which keeps the mapping one to one.
Needs capstone (decompdecomp's Python has it). Made by decompdecomp.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delink  # noqa: E402  the original's side: the same object layout
import names  # noqa: E402  func_<address> / D_<address> -> the original's symbols

try:
    import capstone
    from capstone import x86
except ImportError:  # pragma: no cover
    sys.exit("tools/dll2coff.py needs capstone (pip install capstone): decompdecomp's own Python has it")

DIR32, REL32 = delink.DIR32, delink.REL32
# "// FUNCTION: 0x<va> [symbol substring]" before a definition; the symbol never starts with "(". Comments, blank and
# preprocessor lines, declarations and types may come between it and the definition (the app places the externs
# and prototypes an agent sends there): definition() finds it the way the app's csource.find does.
FUNCTION = re.compile(r"^[ \t]*//[ \t]*FUNCTION:[ \t]*(0x[0-9a-fA-F]+)(?:[ \t]+(?!\(|NONMATCHING\b)(\S+))?[^\n]*(?:\n|$)",
                      re.M)
_COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)


def _blank(text: str) -> str:
    """Same length, with comments and strings blanked, so braces and parentheses inside them don't count."""
    return _COMMENT_OR_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def definition(text: str, clean: str, pos: int, bound: int) -> str | None:
    """The first line of the first function definition in text[pos:bound], up to its "(" (a declaration, a type or
    a brace initialiser before it is skipped), or None."""
    start_pos = pos
    while True:
        brace = clean.find("{", pos, bound)
        if brace < 0:  # no body here: a definition a macro writes (`HEADER_INLINE(void F(int a))`), the first line
            at = start_pos  # with a "(" that isn't a declaration (`;`) or a preprocessor line
            for ln in clean[start_pos:bound].split("\n"):
                s = ln.strip()
                if "(" in s and not s.startswith("#") and not s.endswith(";"):
                    lead = at + len(ln) - len(ln.lstrip())
                    return text[lead:at + ln.rindex("(") + 1]
                at += len(ln) + 1
            return None
        head_at = max(clean.rfind(";", pos, brace), clean.rfind("}", pos, brace), pos - 1) + 1
        head = "\n".join(ln for ln in clean[head_at:brace].split("\n") if not ln.lstrip().startswith("#"))
        if "(" not in head or re.match(r"\s*(?:typedef\s+)?(?:struct|class|union|enum|namespace)\b", head):
            depth, k = 0, brace
            while k < len(clean):  # past the type (or initialiser)
                depth += (clean[k] == "{") - (clean[k] == "}")
                k += 1
                if depth == 0:
                    break
            pos = k
            continue
        at = head_at
        for ln in clean[head_at:brace].split("\n"):  # its first line: the one with the "(" (not a #pragma)
            if "(" in ln and not ln.lstrip().startswith("#"):
                start = at + len(ln) - len(ln.lstrip())
                # up to its last "(" (`__declspec(noinline) bool A::F(`); the real text: extern "C" keeps its "C"
                return text[start:at + ln.rindex("(") + 1]
            at += len(ln) + 1
        return None
TARGET = re.compile(r"//[ \t]*TARGET[ \t]+(0x[0-9a-fA-F]+)[ \t]+(\S+)")
# "// GLOBAL: 0x<va> [map symbol]" then a declaration or definition of the global at that original address
GLOBAL = re.compile(r"//[ \t]*GLOBAL:[ \t]*(0x[0-9a-fA-F]+)(?:[ \t]+(\S+))?[^\n]*\n"
                    r"((?:[ \t]*//(?![ \t]*GLOBAL:)[^\n]*\n|[ \t]*\n)*)((?![ \t]*//)[^\n;={(\[]*)?")
UNRESOLVED = re.compile(r"unresolved external symbol (?:\"[^\"]*\" \()?([^\s()\"]+)\)?")  # tools/ltcg.py's
# LNK2019: "... unresolved external symbol _D_003A1248 referenced in function "public: void __thiscall A::f(void)"
# (?f@A@@QAEXXZ)": which function of ours uses which name the link left unresolved
REFERENCED = re.compile(r"unresolved external symbol (?:\"[^\"]*\" \()?([^\s()\"]+)\)? referenced in function "
                        r"(?:\"[^\"]*\" \()?([^\s()\"]+)\)?")
LITERAL =re.compile(r"__real@([0-9a-fA-F]{8}|[0-9a-fA-F]{16})$")
MAP_LINE = re.compile(r"^\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{8})\s+(\S+)\s+([0-9a-fA-F]{8})\s")


class Image:
    """The harness DLL: sections, image range and base relocation sites."""

    def __init__(self, data: bytes):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        count, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        self.base, self.size = struct.unpack_from("<I", data, opt + 28)[0], struct.unpack_from("<I", data, opt + 56)[0]
        reloc_rva, reloc_size = struct.unpack_from("<II", data, opt + 96 + 5 * 8)
        self.sections = []  # (name, va, virtual size, file offset, raw size)
        for i in range(count):
            at = opt + opt_size + 40 * i
            name = data[at:at + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, raw_size, raw = struct.unpack_from("<4I", data, at + 8)
            self.sections.append((name, self.base + va, vsize, raw, raw_size))
        self.relocs: set[int] = set()
        if reloc_rva:
            table, o = self.read(self.base + reloc_rva, reloc_size), 0
            while o + 8 <= len(table):
                page, size = struct.unpack_from("<II", table, o)
                if size < 8:
                    break
                for k in range(8, size, 2):
                    e = struct.unpack_from("<H", table, o + k)[0]
                    if e >> 12 == 3:  # IMAGE_REL_BASED_HIGHLOW
                        self.relocs.add(self.base + page + (e & 0xFFF))
                o += size

    def section(self, va: int):
        return next((s for s in self.sections if s[1] <= va < s[1] + max(s[2], s[4])), None)

    def offset(self, va: int) -> int | None:
        s = self.section(va)
        return None if s is None or va - s[1] >= s[4] else s[3] + va - s[1]

    def read(self, va: int, n: int) -> bytes:
        s = self.section(va)
        if s is None:
            return b"\0" * n
        off = va - s[1]
        b = self.data[s[3] + off:s[3] + min(off + n, s[4])]
        return b + b"\0" * (n - len(b))

    def contains(self, va: int) -> bool:
        return self.base <= va < self.base + self.size


class Program:
    """The original program (a PE .exe or an Xbox XBE): its bytes at an address, for comparing literal values."""

    def __init__(self, data: bytes):
        self.data, self.sections = data, []  # (va, size in the file, file offset)
        if data[:4] == b"XBEH":
            base, count, at = struct.unpack_from("<I", data, 0x104)[0], *struct.unpack_from("<II", data, 0x11C)
            for i in range(min(count, 256)):
                _, va, _, offset, raw = struct.unpack_from("<5I", data, at - base + 56 * i)
                self.sections.append((va, raw, offset))
        elif data[:2] == b"MZ":
            img = Image(data)
            self.sections = [(va, raw_size, raw) for _, va, _, raw, raw_size in img.sections]

    def read(self, va: int, n: int) -> bytes | None:
        for start, size, offset in self.sections:
            if start <= va and va + n <= start + size:
                return self.data[offset + va - start:offset + va - start + n]
        return None


REPLACE_TRIES, REPLACE_WAIT = 50, 0.1  # Windows refuses to replace a file another process has open


def write_whole(path: Path, data: bytes) -> None:
    """Write a file in one step: the app reads the object and its checks while units build, and a half-written
    checks file once read as "no conflicts", which matched a function with two swapped globals."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT)


def unresolved_by_function(link_said: str) -> dict[str, list[str]]:
    """What the harness link said, as each function of ours (its decorated name) -> the names it left unresolved
    that the function uses."""
    out: dict[str, list[str]] = {}
    for name, function in REFERENCED.findall(link_said):
        if name not in out.setdefault(function, []):
            out[function].append(name)
    return out


def unresolved_name(symbols: list[str]) -> str:
    """What a reference the link left unresolved is called in the diff: the source name of what the function uses
    unresolved (`$unresolved:D_003A1248`; several: `$unresolved:D_003A1248|D_003A1250`), never an address. It read
    `$A10000000` (the DLL's image base, where /FORCE:UNRESOLVED puts them all), and agents couldn't tell which global
    or constant a row meant. (A `$` name never stands for an original address: export.py's ADDRESS_NAME.)"""
    shown = []
    for s in symbols:
        s = s[1:].split("@", 1)[0] if s.startswith("?") else re.sub(r"@\d+$", "", s[1:] if s[:1] in "_@" else s)
        if s and s not in shown:
            shown.append(s)
    return "$unresolved:" + "|".join(shown[:4]) if shown else "$unresolved"


def map_symbols(path: Path) -> dict[str, int]:
    """Every symbol of the linker's map (public and static) -> its address."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = MAP_LINE.match(line)
        if m and int(m.group(1), 16):
            out.setdefault(m.group(3), int(m.group(4), 16))
    return out


def map_objects(path: Path) -> dict[str, str]:
    """Every symbol of the linker's map -> the object it comes from (the line's last word: `00_unit.obj`)."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = MAP_LINE.match(line)
        if m and int(m.group(1), 16):
            out.setdefault(m.group(3), line.split()[-1])
    return out


def own_objects(sources: list[Path]) -> set[str]:
    """The objects of the unit's own code in its harness link: ltcg.py's <NN>_<file>.obj of every source that isn't in
    harness/ (the unit's source and the files its code is split into), numbered in link order as ltcg.py numbers them."""
    return {f"{i:02d}_{p.stem.replace('.', '_')}.obj" for i, p in enumerate(sources)
            if not p.parts or p.parts[0] != "harness"}


def own_symbol(syms: dict[str, int], objects: dict[str, str], name: str, own: set[str]) -> str | None:
    """`name` (a unit function's original, decorated name) when the map has it from one of the unit's own objects
    (own_objects), not from a stand-in: the function an annotation means when the definition right after the
    annotation is something else, such as an inline helper written between the two."""
    return name if name in syms and any(objects.get(name, "").endswith(o) for o in own) else None


def symbol_hint(decl: str) -> str | None:
    """The map symbol substring for a definition line: `bool Car::IsFast(` -> `?IsFast@Car@@` (MSVC mangles A::B::f
    as ?f@B@A@@ and a free function f as ?f@@). Operators, constructors and destructors need an explicit one."""
    head = decl[:decl.rindex("(")].strip()
    name = re.split(r"[\s*&]+", head)[-1]
    parts = name.split("::")
    last = parts[-1]
    if not last or last.startswith("operator") or last.startswith("~") or (len(parts) > 1 and last == parts[-2]):
        return None
    if re.search(r'extern\s+"C"', decl):
        return None
    return "?" + "@".join([last] + parts[-2::-1]) + "@@"


def annotations(paths: list[Path]) -> tuple[dict[int, str], list[str]]:
    """original address -> map symbol substring, from the sources' FUNCTION and TARGET annotations; and problems."""
    out, problems = {}, []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        for va, sym in TARGET.findall(text):
            out[int(va, 16)] = sym
        clean = _blank(text)
        found = list(FUNCTION.finditer(text))
        for n, m in enumerate(found):
            va, sym = m.group(1), m.group(2)
            if sym and "::" in sym:  # `// FUNCTION: 0x29080 Mode::Tick (NONMATCHING ...)`: the source's name, which
                sym = symbol_hint(sym + "(")  # no decorated map symbol contains; looked for as "Mode::Tick" it never
            bound = found[n + 1].start() if n + 1 < len(found) else len(text)  # was found, and never compiled in
            decl = None if sym else definition(text, clean, m.end(), bound)
            if not sym and decl is None:
                problems.append(f"{p}: FUNCTION {va}: no function definition follows it")
                continue
            sym = sym or symbol_hint(decl)
            if sym is None:
                problems.append(f"{p}: FUNCTION {va}: give the map symbol after the address (an operator, "
                                "constructor, destructor or extern \"C\" function)")
                continue
            out.setdefault(int(va, 16), sym)
    return out, problems


def global_annotations(paths: list[Path]) -> dict[str, int]:
    """Map symbol (or the declared name) -> original address, from `// GLOBAL: 0x<address> [symbol]` before a
    global's declaration: for globals with real names, which (unlike D_<address>) don't say where they are."""
    out = {}
    for p in paths:
        for va, sym, _, decl in GLOBAL.findall(p.read_text(encoding="utf-8", errors="replace")):
            names_in = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", decl)
            if sym or names_in:
                out.setdefault(sym or names_in[-1], int(va, 16))
    return out


def global_symbol(syms: dict[str, int], name: str) -> str | None:
    """The map symbol of a global declared as `name`: `name` itself, its C name `_name` or its C++ name `?name@...`."""
    if name in syms:
        return name
    cand = [s for s in syms if s == f"_{name}" or s.startswith(f"?{name}@")]
    return cand[0] if len(cand) == 1 else None


SCALARS = {"C": 1, "D": 1, "E": 1, "F": 2, "G": 2, "H": 4, "I": 4, "J": 4, "K": 4, "M": 4, "N": 8, "_J": 8, "_K": 8,
           "_N": 1, "_W": 2}


def scalar_size(symbol: str) -> int | None:
    """The size of a C++ global of a plain type, from its decorated name (`?g_x@@3MB`: a const float, 4); None for
    structs, arrays, pointers and C names, whose size the name doesn't say."""
    m = re.fullmatch(r"\?[^?@][^@]*@@3(_?[A-Z])[AB]", symbol)
    return SCALARS.get(m.group(1)) if m else None


def _show(data: bytes, name: str) -> str:
    if name.startswith("__real@") and len(data) == 4:
        return f"{struct.unpack('<f', data)[0]!r}f"
    if name.startswith("__real@") and len(data) == 8:
        return f"{struct.unpack('<d', data)[0]!r}"
    if name.startswith("??_C@_1"):
        return "L" + json.dumps(data.decode("utf-16-le", "replace").rstrip("\0"))
    return json.dumps(data.rstrip(b"\0").decode("latin-1"))


def bounds(md, img: Image, start: int, end: int) -> set[int]:
    """Offsets of the addresses `cmp` compares with: a loop's end pointer is one past an array, where in our link
    another global (or one the map doesn't name) may start."""
    out = set()
    for ins in md.disasm(img.read(start, code_end(md, img, start, end) - start), start):
        if ins.id == x86.X86_INS_CMP:
            out.update(ins.address - start + f for f, n in ((ins.imm_offset, ins.imm_size), (ins.disp_offset,
                                                                                           ins.disp_size)) if n == 4)
    return out


def data_check(fn: dict, exe: bytes, program: Program, ours: list[tuple[int, int, int]], lo: int, hi: int,
               claim, literal, loose: set[int] = frozenset()) -> dict:
    """What each reference stands for, against what the original's reference at the same place is. objdiff
    passes a function that reads one global where the original reads another (each used once, so the address
    mapping stays one to one), but our references often say which original address they mean: a D_<address> or
    func_<address> name, a `// GLOBAL:` annotation, one of the unit's own functions. Compiler literals (floats,
    strings) are compared by value. `claim(our address)` -> ([(name, the original address it may stand for)], sure):
    one must be the original's (an address where one global starts can also end the one before it), and only a
    `sure` claim (our address is the symbol's own) that doesn't is a finding, unless `cmp` compares with it (offsets
    in `loose`: an end pointer); `literal(our address)` -> (name, its
    bytes) or None. References that say neither aren't checked."""
    orig = original_targets(fn, exe)
    checked, wrong = 0, []
    for off, _, value in ours:
        theirs = orig.get(off)
        if theirs is None or theirs[1] is None or lo <= value < hi:
            continue
        a = theirs[1]
        c, sure = claim(value) or ([], False)
        if c and any(addr == a for _, addr in c):
            checked += 1
        elif c and sure and off not in loose:
            checked += 1
            wrong.append(f"+0x{off:X}: yours refers to {c[0][0]} (0x{c[0][1]:X}) where the original refers to 0x{a:X}")
        elif c:
            continue  # past a symbol's start, into something the map may not name: no verdict
        elif (lit := literal(value)) is not None:
            checked += 1
            got = program.read(a, len(lit[1]))
            if got != lit[1]:
                was = _show(got, lit[0]) if got is not None else "nothing it can read"
                wrong.append(f"+0x{off:X}: yours loads {_show(lit[1], lit[0])} ({lit[0]}) where the original loads {was} "
                             f"(0x{a:X})")
    return {"data_checked": checked, "data": wrong}


def find_symbol(syms: dict[str, int], want: str) -> tuple[str | None, str]:
    cand = [s for s in syms if want in s]
    if len(cand) > 1 and want in syms:  # an exact name wins over longer names that contain it
        cand = [want]
    if len(cand) == 1:
        return cand[0], ""
    return None, "isn't in the map (not called from the driver, or inlined everywhere)" if not cand else \
        f"is ambiguous in the map: {', '.join(sorted(cand)[:4])}"


def extent(img: Image, start: int, starts: list[int]) -> int:
    """Where the function at `start` ends: the next symbol (or its section's end), less the linker's int3 padding."""
    sec = img.section(start)
    limit = sec[1] + sec[2]
    i = bisect.bisect_right(starts, start)
    if i < len(starts):
        limit = min(limit, starts[i])
    code = img.read(start, limit - start).rstrip(b"\xcc")
    return start + max(1, len(code))


def code_end(md, img: Image, start: int, end: int) -> int:
    """Where the instructions end: the first jump table (`jmp [reg*4 + table]`) or byte index table inside the
    function, as scratch_pe reads the original's (MSVC puts both after the code)."""
    ends = [end]
    for ins in md.disasm(img.read(start, end - start), start):
        for op in ins.operands:
            if op.type != x86.X86_OP_MEM or ins.disp_size != 4:
                continue
            disp = op.mem.disp & 0xFFFFFFFF
            if not start < disp < end or disp <= ins.address:
                continue
            if (op.mem.scale == 4 and ins.id == x86.X86_INS_JMP) or op.size == 1:
                ends.append(disp)
    return min(ends)


def cut(md, img: Image, start: int, end: int, name_of) -> tuple[list, list[str], list[tuple[int, int, int]]]:
    """(relocations in delink.py's form, notes, (offset, type, our target address) for the mapping check)."""
    relocs, notes, targets, seen = [], [], [], set()
    stop = code_end(md, img, start, end)
    for ins in md.disasm(img.read(start, stop - start), start):
        at = ins.address - start
        if (x86.X86_GRP_JUMP in ins.groups or x86.X86_GRP_CALL in ins.groups) and ins.operands \
                and ins.operands[0].type == x86.X86_OP_IMM:
            target = ins.operands[0].imm & 0xFFFFFFFF
            if start <= target < end:
                continue
            if ins.size < 5:
                notes.append(f"+0x{at:X}: a short jump leaves the function (to 0x{target:X}): no relocation can "
                             "say where")
                continue
            off = at + ins.size - 4
            name, addend = name_of(target)
            relocs.append((off, REL32, name, addend))
            targets.append((off, REL32, target))
            seen.add(start + off)
            continue
        for field, size in ((ins.disp_offset, ins.disp_size), (ins.imm_offset, ins.imm_size)):
            if size == 4 and field and img.contains(struct.unpack_from("<I", bytes(ins.bytes), field)[0]) \
                    and ins.address + field not in img.relocs:
                notes.append(f"+0x{at + field:X}: an address-like constant without a base relocation")
    for site in sorted(r for r in img.relocs if start <= r < end - 3):
        if site in seen:
            continue
        off = site - start
        value = struct.unpack("<I", img.read(site, 4))[0]
        if start <= value < end:
            relocs.append((off, DIR32, None, value - start))  # a label: a jump table, its entries
        else:
            name, addend = name_of(value)
            relocs.append((off, DIR32, name, addend))
        targets.append((off, DIR32, value))
    relocs.sort()
    targets.sort()
    return relocs, notes, targets


def same_constants(fn: dict, exe: bytes, code: bytes, relocs: list) -> tuple[list, list[int]]:
    """Our relocations plus the original's where it relocates a field that ours holds as the very same number: a
    constant the original's delinker took for an address (Xbox programs load at 0x10000, so struct offsets like
    `cmp eax, [ecx+0xE6C80]`, masks and small immediates fall inside the image; in the harness DLL, at 0x10000000,
    they are plain numbers). An address our link made can't equal the original's, so equal bytes with no base
    relocation here mean the original's field is that number too. Both sides then carry the same relocation, as
    objdiff needs; the export resolves it to the same number. Returns (relocations, the offsets taken over)."""
    taken = [o for o, *_ in relocs]
    added = []
    for off, rtype, name, addend in fn["relocs"]:
        if rtype != DIR32 or name is None or off + 4 > len(code) or any(abs(o - off) < 4 for o in taken):
            continue
        if code[off:off + 4] == exe[fn["offset"] + off:fn["offset"] + off + 4]:
            added.append((off, DIR32, name, addend))
            taken.append(off)
    return sorted(relocs + added), sorted(o for o, *_ in added)


def unresolved_as_original(fn: dict, relocs: list, targets: list[tuple[int, int, int]],
                           image_base: int, headers: int = 1) -> tuple[list, set[int]]:
    """References the harness link left unresolved (/FORCE:UNRESOLVED puts them at the DLL's image base: functions
    of the unit nobody has written yet, or what only they call), written as the original's reference at the same
    place. A field of an unresolved global (`D_x.y`, `D_x[2]`) lands past the base by its offset, inside the DLL's
    headers (`headers`: where its first section starts), where nothing of ours is: two such fields at the same offset
    of two globals would otherwise read as one of ours mapped to two of the original's. They can't be checked, so
    the mapping and data checks leave them out (as the LTCG harness does), and the function lists them
    (`unresolved`). Whether one changes the code around it is another matter: the brief and compile_and_diff say
    which can (callees the original passes registers or inlines). Returns (relocations, their offsets)."""
    orig = {off: (rtype, name, addend) for off, rtype, name, addend in fn["relocs"]}
    wild = {off for off, _, value in targets if image_base <= value < image_base + max(1, headers)}
    out = []
    for r in relocs:
        off, rtype = r[0], r[1]
        if off in wild and off in orig and orig[off][0] == rtype and orig[off][1] is not None:
            out.append((off, rtype, orig[off][1], orig[off][2]))
        else:
            out.append(r)
    return out, wild


ADDRESS_NAME = re.compile(r"^[_@]?(?:D|func)_([0-9A-Fa-f]{8})(?:@\d+)?$")  # export.py's: the address it means


def in_original_terms(md, code: bytes, start: int, fn: dict, exe: bytes, relocs: list,
                      annotated: dict[str, int]) -> list:
    """Our relocations, those after the first in an instruction written as the original's (name and addend).
    objdiff relates one relocation per instruction and compares the other fields' bytes, which hold the addend:
    `mov dword ptr [g_w+0x28], offset g_x+0x4C` against the original's `[D_003FE4E0], offset D_0073A1D0` differs
    in the immediate even when both mean the same addresses. Which addresses they mean is the mapping and data
    checks' job, so a field is rewritten only where the export would take it for the original's anyway: a name
    that says its address (D_/func_<address>, a `// GLOBAL:`) must say the original's, other names pair by place."""
    orig = {off: (name, addend) for off, rtype, name, addend in fn["relocs"] if rtype == DIR32 and name is not None}
    ends = sorted((ins.address - start, ins.address - start + ins.size) for ins in md.disasm(code, start))
    out, last = [], None
    for r in relocs:
        off, rtype, name, addend = r
        k = bisect.bisect_right(ends, (off, 1 << 30)) - 1
        span = ends[k] if k >= 0 and ends[k][0] <= off < ends[k][1] else None
        first, last = span is None or span != last, span
        if first or rtype != DIR32 or name is None or off not in orig:
            out.append(r)
            continue
        want = struct.unpack_from("<I", exe, fn["offset"] + off)[0]
        m = ADDRESS_NAME.match(name)
        said = int(m.group(1), 16) if m else annotated.get(name)
        if said is not None and (said + addend) & 0xFFFFFFFF != want:
            out.append(r)  # it says another address: left for the data check (and the export) to find
            continue
        out.append((off, rtype, *orig[off]))
    return out


def original_targets(fn: dict, exe: bytes) -> dict[int, tuple[int, int | None]]:
    """offset -> (type, the original's target address; None inside the function) of its relocations."""
    out = {}
    for off, rtype, name, _ in fn["relocs"]:
        raw = struct.unpack_from("<I", exe, fn["offset"] + off)[0]
        addr = raw if rtype == DIR32 else (fn["addr"] + off + 4 + raw) & 0xFFFFFFFF
        out[off] = (rtype, None if name is None else addr)
    return out


def mapping(fn: dict, exe: bytes, ours: list[tuple[int, int, int]], lo: int, hi: int,
            skip: set[int] = frozenset(), folded=None) -> dict:
    """The harness's address check: relocation by relocation the original's target and ours make a pair; one
    original address must always pair with the same one of ours and the other way round. References inside the
    function (jump tables) are compared by offset. `skip`: the original's relocations that are constants in both
    (same_constants). `folded(original, ours, ours too)`: the original's address is one vtable that the game's
    whole-program link folded (identical COMDAT folding) where ours keeps two, which a harness link can't
    reproduce: a note ("folded"), not a conflict, as the LTCG harness has it."""
    orig = {off: t for off, t in original_targets(fn, exe).items() if off not in skip}
    fwd: dict[int, int] = {}
    back: dict[int, int] = {}
    conflicts, unpaired, pairs, notes = [], [], 0, []
    for off, rtype, value in ours:
        theirs = orig.pop(off, None)
        if theirs is None:
            unpaired.append(f"+0x{off:X}: a relocation in yours only")
            continue
        a, b = theirs[1], (None if lo <= value < hi else value)
        if a is not None and value == lo:  # a callback or a recursive reference: ours names the function itself
            conflicts.append(f"+0x{off:X}: yours refers to this function itself, the original to 0x{a:X}")
            continue
        if (a is None) != (b is None):
            conflicts.append(f"+0x{off:X}: {'inside' if a is None else 'outside'} the function in the original, "
                             f"{'inside' if b is None else 'outside'} it in yours")
            continue
        if a is None:  # inside the function: a jump table entry (or its address) must go to the same place
            want = struct.unpack_from("<I", exe, fn["offset"] + off)[0] - fn["addr"]
            if rtype == DIR32 and value - lo != want:
                conflicts.append(f"+0x{off:X}: goes to +0x{value - lo:X} in yours, +0x{want:X} in the original "
                                 "(a jump table entry)")
            continue
        pairs += 1
        if fwd.setdefault(a, b) != b:
            text = f"orig 0x{a:X} -> yours 0x{fwd[a]:X} and 0x{b:X}"
            (notes if folded is not None and folded(a, fwd[a], b) else conflicts).append(text)
        if back.setdefault(b, a) != a:
            conflicts.append(f"yours 0x{b:X} <- orig 0x{back[b]:X} and 0x{a:X}")
    unpaired += [f"+0x{off:X}: a relocation in the original only" for off in sorted(orig)]
    return {"pairs": pairs, "conflicts": sorted(set(conflicts)), "unpaired": unpaired, "folded": sorted(set(notes))}


def original_vtables(config: Path) -> set[int]:
    """The original's vtable addresses a project lists beside functions.json: vtables.json (a list of addresses,
    numbers or "0x..." strings) or vtables.csv (the address in the first column, a header line allowed)."""
    out: set[int] = set()
    js, csv = config.with_name("vtables.json"), config.with_name("vtables.csv")
    if js.is_file():
        out.update(int(v, 0) if isinstance(v, str) else int(v) for v in json.loads(js.read_text(encoding="utf-8")))
    if csv.is_file():
        for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
            head = line.split(",", 1)[0].strip()
            if re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", head):
                out.add(int(head, 0))
    return out


def vtable_fold(listed: set[int], starts: set[int], exe_word, our_name):
    """folded() for mapping(): both of ours are vtables (MSVC names them ??_7Class@@6B@) and the original's is
    one, by the project's list or because its first word points at one of the game's functions."""
    def folded(a: int, b1: int, b2: int) -> bool:
        if not all(our_name(b)[0].startswith("??_7") and our_name(b)[1] == 0 for b in (b1, b2)):
            return False
        return a in listed or exe_word(a) in starts
    return folded


def main() -> None:
    config, unit, dll, mapf, out = sys.argv[1:6]
    sources = [Path(p) for p in sys.argv[6:]]
    info = json.loads(Path(config).read_text(encoding="utf-8"))
    exe = Path(info["exe"]).read_bytes()
    if hashlib.sha1(exe).hexdigest() != info["sha1"]:
        sys.exit(f"{info['exe']} isn't the copy this project was made from (its SHA-1 should be {info['sha1']})")
    functions = {fn["addr"]: fn for fn in info["units"][unit]}
    unit_of = {fn["addr"]: u for u, fns in info["units"].items() for fn in fns}
    img = Image(Path(dll).read_bytes())
    syms = map_symbols(Path(mapf))
    objects = map_objects(Path(mapf))
    wanted = names.wanted(info)
    real = names.real_names(info)  # functions.json's "names": a real name stands for its address, as in names.py

    def key_of(s: str) -> bytes | None:
        """func_XXXXXXXX / D_XXXXXXXX a map symbol stands for, by the address in its name or its real name."""
        return names.address_of(s.encode(), real)

    starts =sorted({a for a in syms.values() if img.section(a) is not None})
    by_addr = sorted((a, s) for s, a in syms.items())
    addrs = [a for a, _ in by_addr]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True

    hints, problems = annotations(sources)
    for va in sorted(hints):
        if va not in functions:
            where = f"it is in unit {unit_of[va]}" if va in unit_of else "no function starts there"
            problems.append(f"0x{va:08X} ({hints[va]}) isn't one of {unit}'s functions: {where}")
    found: dict[int, tuple[str, int]] = {}  # original address -> (map symbol, our address)
    for addr, fn in functions.items():
        want = hints.get(addr)
        if want is None:
            m = [s for s in syms if key_of(s) == f"func_{addr:08X}".encode()]
            if len(m) > 1:  # a real name library code has too (an XDK class's Add): the unit's own object's
                own = own_objects(sources)
                m = [s for s in m if any(objects.get(s, "").endswith(o) for o in own)]
            if len(m) == 1:
                found[addr] = (m[0], syms[m[0]])
            continue
        sym, why = find_symbol(syms, want)
        if sym is None:  # `// FUNCTION:`, then a helper inlined into the function, then the function itself
            sym = own_symbol(syms, objects, fn["name"], own_objects(sources))
        if sym is None:
            problems.append(f"0x{addr:08X} {want} {why}")
        else:
            found[addr] = (sym, syms[sym])
    ours_name = {sva: functions[a]["name"] for a, (_, sva) in found.items()}

    headers = min((s[1] for s in img.sections), default=img.base + 0x1000) - img.base  # where nothing of ours is
    log = Path(f"{mapf}.link.txt")  # what the harness link said (tools/ltcg.py link)
    link_said = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    referenced = unresolved_by_function(link_said)

    def name_of(va: int, function: str | None = None) -> tuple[str, int]:
        if va in ours_name:
            return ours_name[va], 0
        if img.base <= va < img.base + headers:  # left unresolved: the names the link says this function uses
            return unresolved_name(referenced.get(function or "", [])), va - img.base
        i = bisect.bisect_right(addrs, va) - 1
        if i < 0:
            return f"$A{va:08X}", 0
        a, s = by_addr[i]
        if names.REF.search(s.encode()):
            s = wanted.get(key_of(s), s.encode()).decode()
        elif (key := key_of(s)) is not None:  # a real name: as names.py names it, the original's or _D_<address>
            s = wanted.get(key, b"_" + key).decode()
        return s, va - a

    program = Program(exe)
    orig_of = {sva: a for a, (_, sva) in found.items()}  # our copy of a unit function -> its original address
    annotated = {}
    for name, va in global_annotations(sources).items():
        if (sym := global_symbol(syms, name)) is not None:
            annotated[sym] = va

    def stands_for(i: int, va: int) -> tuple[str, int] | None:
        """The original address our address `va` means, read as an offset into map symbol `i`."""
        a, s = by_addr[i]
        delta = va - a
        size = scalar_size(s)
        if delta >= (size + 1 if size else 0x1000) or img.section(a) is not img.section(va):
            return None  # past a scalar (and its one-past-the-end): something the map doesn't name
        shown = s if not delta else f"{s}+0x{delta:X}"
        if s in annotated:
            return shown, annotated[s] + delta
        key = key_of(s)  # (a real name too: a renamed global is checked as its address was)
        return (shown, int(key.split(b"_", 1)[1], 16) + delta) if key else None

    def claim(va: int) -> tuple[list[tuple[str, int]], bool]:
        if va in orig_of:
            return [(functions[orig_of[va]]["name"], orig_of[va])], True
        i = bisect.bisect_right(addrs, va) - 1
        if i < 0 or (first := stands_for(i, va)) is None:
            return [], False
        out = [first]
        if i > 0 and by_addr[i][0] == va and (c := stands_for(i - 1, va)) is not None:
            out.append(c)  # where one global starts, the one before it may end: a loop's end pointer
        # Only a reference to a symbol itself is sure: past its start may be data the map doesn't name (C++ const
        # globals and statics have internal linkage, and the map lists public symbols)
        return out, by_addr[i][0] == va

    def literal(va: int) -> tuple[str, bytes] | None:
        i = bisect.bisect_right(addrs, va) - 1
        if i < 0 or by_addr[i][0] != va:
            return None
        s = by_addr[i][1]
        if (m := LITERAL.match(s)) is not None:
            return s, bytes.fromhex(m.group(1))[::-1]
        if s.startswith("??_C@_0") or s.startswith("??_C@_1"):
            raw, wide = img.read(va, 512), s.startswith("??_C@_1")
            step = 2 if wide else 1
            end = next((k for k in range(0, len(raw) - step + 1, step) if raw[k:k + step] == b"\0" * step), None)
            return None if end is None else (s, raw[:end + step])
        return None

    def word(va: int) -> int | None:
        raw = program.read(va, 4)
        return None if raw is None else struct.unpack("<I", raw)[0]

    folded = vtable_fold(original_vtables(Path(config)), set(unit_of), word, name_of)
    cuts, report = [], {"unit": unit, "dll": dll, "functions": {}, "problems": problems}
    for addr in sorted(found):
        sym, start = found[addr]
        end = extent(img, start, starts)
        relocs, notes, targets = cut(md, img, start, end, lambda va, sym=sym: name_of(va, sym))
        fn = functions[addr]
        code = img.read(start, end - start)
        relocs, constants = same_constants(fn, exe, code, relocs)
        relocs = in_original_terms(md, code[:code_end(md, img, start, end) - start], start, fn, exe, relocs,
                                   annotated)
        relocs, wild = unresolved_as_original(fn, relocs, targets, img.base, headers)
        cuts.append({"name": fn["name"], "addr": addr, "offset": img.offset(start), "size": end - start,
                     "relocs": relocs})
        known = [t for t in targets if t[0] not in wild]
        check = mapping(fn, exe, known, start, end, skip=set(constants) | wild, folded=folded)
        check["constants"] = [f"+0x{o:X}" for o in constants]
        check["unresolved"] = [f"+0x{o:X}" for o in sorted(wild)]
        check |= data_check(fn, exe, program, known, start, end, claim, literal, bounds(md, img, start, end))
        report["functions"][fn["name"]] = {"orig": f"0x{addr:08X}", "ours": f"0x{start:08X}", "symbol": sym,
                                           "size": end - start, "orig_size": fn["size"], "notes": notes, **check}
    if link_said:
        found_unresolved = sorted(set(UNRESOLVED.findall(link_said)))
        if found_unresolved:
            report["unresolved"] = found_unresolved
            print(f"{unit}: nothing in the harness link defines these yet, so it left them unresolved: "
                  f"{', '.join(found_unresolved[:12])}{' ...' if len(found_unresolved) > 12 else ''}. The judge writes "
                  "the original's reference in their place and leaves them out of the address checks. Callees the "
                  "original calls with a standard convention are harmless. Data (globals, pool constants) can still "
                  "change the code around it under LTCG: define it with its real type. A callee the original passes "
                  "arguments in registers (LTCG) or inlines changes the code that uses it until a definition is in "
                  f"the link (in the unit, or a stand-in shaped like the original's in harness/{unit}.<name>.cpp); "
                  "the brief and compile_and_diff say which those are.")
    data = delink.coff(cuts, img.data)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_whole(path, data)
    write_whole(path.with_suffix(".ltcg.json"), json.dumps(report, indent=1).encode("utf-8"))
    bad = {n: f["conflicts"] for n, f in report["functions"].items() if f["conflicts"]}
    for p in problems:
        print(f"{unit}: {p}")
    for n, c in bad.items():
        print(f"{unit}: {n}: addresses map inconsistently: {'; '.join(c[:4])}")
    for n, f in report["functions"].items():
        if f["data"]:
            print(f"{unit}: {n}: refers to other data than the original: {'; '.join(f['data'][:4])}")


if __name__ == "__main__":
    main()
