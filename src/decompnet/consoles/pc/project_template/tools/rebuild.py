#!/usr/bin/env python3
"""Put the game's program back together from this project, and check it against your copy byte for byte.

    python tools/rebuild.py config/<version>/functions.json build/<version>      (or: ninja rebuild)

Every function's code goes back to its address in the program: what src/ compiles to (build/<version>/src/auto/
<unit>.obj) where that is the game's code once its relocations are filled in, else the function as tools/delink.py
cut it out of your copy (build/<version>/target/<unit>.obj), its relocations filled in again. Relocations are filled
in from the project's own symbols: every function's address (functions.json), data by the address in its name
(D_00402034, a 64-bit program's D_180003020), imports by their slot. A 64-bit program (PE32+, AMD64) rebuilds the
same way: its header, symbols and relocations spell their own 64-bit forms, filled to the same bytes. A constant
the C has of its own (a string, a float) is used where your copy has
the same bytes, at the address the game's code reads it from. The rest of the program (its headers, data, imports
and resources) is your copy's: only the code is decompiled so far, and the program is rebuilt at the addresses the
game has, which is what lets it come out identical (a linker would have to place every object, library and resource
exactly as the original's did). The game's own DLLs (functions.json's "modules") are rebuilt the same way, each from
its own source files and against its own copy: at the address the project gives it, then moved back to its own by its
base relocations. Writes build/<version>/<program>.exe (and each DLL where it is in orig/; an Xbox game's default.xbe
beside it, out of the program decompdecomp made of it) and rebuild.json (which
functions came from C); exits with 1 when a program isn't your copy byte for byte. Standard library only. Made by
decompdecomp.
"""

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

DIR32, REL32 = 0x06, 0x14
# AMD64's COFF relocation kinds (what an x64 program's objects carry; x86's DIR32 and REL32 above): ADDR64 an
# absolute address in 8 bytes, ADDR32NB the address relative to the image's base, REL32..REL32_5 a PC-relative
# 4-byte displacement measured from the byte after the field plus kind - 4 more on (a RIP-relative operand's
# displacement starts mid-instruction). The machines number their kinds their own ways: AMD64's REL32_2 has x86's
# DIR32's number, so which machine spells a kind comes from the program, never from the number alone.
ADDR64, ADDR32NB = 0x01, 0x03
REL32S = range(0x04, 0x0A)
# what a PE32+ (a 64-bit program's) optional header says where a PE32's says it too, only elsewhere: its image base
# in 8 bytes at 24 (a PE32's is 4 at 28), its data directories at 112 and so its import directory at 120 (96, 104)
PE32PLUS_MAGIC = 0x20B
PE32_IMAGE_BASE_AT, PE32PLUS_IMAGE_BASE_AT = 28, 24
PE32_IMPORTS_AT, PE32PLUS_IMPORTS_AT = 104, 120
FUNCTION = 0x20  # a symbol's type when it's a function
XBE_MARK = 0x28  # where an Xbox game's view says where its XBE is (decompdecomp's consoles/xbox/scratch.py pe_view)
PADDING = b"\x90\xcc"  # what pads a function (Visual C++ 6's nops) or aligns the next one (the linker's int3)
# data by the address in its name: x86 spells it _D_00402034, x64 D_180003020 (an image based at 0x140000000 or
# 0x180000000 gives its data names a ninth hex digit), so 6 to 16 digits, all of the name (a fullmatch)
DATA_SYMBOL = re.compile(r"_?D_([0-9A-F]{6,16})")
# symbols the C runtime defines as numbers, not places: __except_list is the thread's exception handler chain, fs:[0],
# which every function with C++ exception handling (/GX) reads and writes
ABSOLUTE = {"__except_list": 0}
SHOWN = 5  # functions a rebuild that differs names


def read_coff(obj: bytes) -> tuple[list[dict], list[tuple | None]]:
    """A COFF object's sections ({"name", "data" (None: uninitialised), "size", "relocs": [(offset, symbol index,
    type)]}) and symbols by index ((name, value, section number, type); None for an auxiliary record)."""
    _, count, _, table, nsyms, opt, _ = struct.unpack_from("<HHIIIHH", obj, 0)
    strings = table + 18 * nsyms
    symbols, k = [], 0
    while k < nsyms:
        raw, value, section, typ, _, aux = struct.unpack_from("<8sIhHBB", obj, table + 18 * k)
        if raw[:4] == b"\0\0\0\0":
            at = strings + struct.unpack_from("<I", raw, 4)[0]
            raw = obj[at:obj.index(b"\0", at)]
        symbols.append((raw.rstrip(b"\0").decode("latin-1"), value, section, typ))
        symbols.extend([None] * aux)
        k += 1 + aux
    sections = []
    for i in range(count):
        name, _, _, size, data, relocs, _, nrelocs, _, _ = struct.unpack_from("<8sIIIIIIHHI", obj, 20 + opt + 40 * i)
        sections.append({"name": name.rstrip(b"\0").decode("latin-1"), "size": size,
                         "data": obj[data:data + size] if data else None,
                         "relocs": [struct.unpack_from("<IIH", obj, relocs + 10 * r) for r in range(nrelocs)]})
    return sections, symbols


class Program:
    """Your copy of the game's program and the project's symbols for its addresses."""

    def __init__(self, info: dict, exe: bytes):
        self.exe = exe
        at = struct.unpack_from("<I", exe, 0x3C)[0]
        count, opt = struct.unpack_from("<H", exe, at + 6)[0], struct.unpack_from("<H", exe, at + 20)[0]
        # PE32+ (a 64-bit program) says where its image prefers in 8 bytes at its optional header's 24, a PE32 in
        # 4 at 28 -- and its symbols and relocations spell AMD64's, not x86's (fill, _imports)
        self.x64 = _pe32plus(exe)
        self.base = base = struct.unpack_from("<Q" if self.x64 else "<I", exe,
                                              at + 24 + (PE32PLUS_IMAGE_BASE_AT if self.x64 else PE32_IMAGE_BASE_AT))[0]
        self.sections = []  # (address, size in the file, offset in the file)
        for i in range(count):
            _, va, size, offset = struct.unpack_from("<IIII", exe, at + 24 + opt + 40 * i + 8)
            self.sections.append((base + va, size, offset))
        self.where = {fn["name"]: fn["addr"] for fns in info["units"].values() for fn in fns}
        self.where.update(self._imports(exe, at, base))

    def offset(self, addr: int) -> int | None:
        return next((offset + addr - va for va, size, offset in self.sections if va <= addr < va + size), None)

    def read(self, addr: int, n: int) -> bytes:
        off = self.offset(addr)
        return b"" if off is None else self.exe[off:off + n]

    def _imports(self, exe: bytes, at: int, base: int) -> dict[str, int]:
        """__imp__<name> -> its import address table slot, as the project's analysis names them (x64: __imp_<name>,
        its 8-byte entries and slots, an ordinal in bit 63 instead of 31)."""
        out, rva = {}, struct.unpack_from("<I", exe, at + 24 + (PE32PLUS_IMPORTS_AT if self.x64 else PE32_IMPORTS_AT))[0]
        width, ordinal = (8, 0x8000000000000000) if self.x64 else (4, 0x80000000)
        desc = base + rva
        while rva and len(d := self.read(desc, 20)) == 20 and d != bytes(20):
            lookup, _, _, name_rva, iat = struct.unpack("<IIIII", d)
            dll = self.read(base + name_rva, 64).split(b"\0", 1)[0].decode("latin-1")
            k = 0
            while len(e := self.read(base + (lookup or iat) + width * k, width)) == width \
                    and (value := int.from_bytes(e, "little")):
                if value & ordinal:
                    name = f"{Path(dll).stem}_{value & 0xFFFF}"
                else:
                    name = self.read(base + value + 2, 256).split(b"\0", 1)[0].decode("latin-1")
                out[f"{'__imp_' if self.x64 else '__imp__'}"
                    f"{name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) else f'{Path(dll).stem}_{k}'}"] = \
                    base + iat + width * k
                k += 1
            desc += 20
        return out

    def width(self, kind: int) -> int:
        """How many bytes a relocation of this kind fills in: AMD64's ADDR64 is 8, every other kind 4."""
        return 8 if self.x64 and kind == ADDR64 else 4

    def address(self, name: str) -> int | None:
        """The address a symbol names: a function, data by its address, an import (as C declares it too:
        __imp__Sleep@4 is the slot of Sleep; x64 names it __imp_Sleep), or x64's __ImageBase: the program's own
        base, which every switch's code loads (`lea reg, [rip + __ImageBase]`)."""
        if name in self.where:
            return self.where[name]
        if name == "__ImageBase":
            return self.base
        if name in ABSOLUTE:
            return ABSOLUTE[name]
        if m := DATA_SYMBOL.fullmatch(name):
            return int(m.group(1), 16)
        return self.where.get(re.sub(r"@\d+$", "", name)) if name.startswith("__imp_") else None


def _anchored(program: Program, kind: int, at: int, off: int) -> int:
    """What a relocation's filled-in value is measured from: x86's REL32 the byte after its 4-byte field, AMD64's
    REL32_n (kinds 4..9) n = kind - 4 bytes further on (a RIP-relative displacement starts mid-instruction),
    AMD64's ADDR32NB the image's base; DIR32 and ADDR64 nothing, the address itself."""
    if not program.x64:
        return at + off + 4 if kind == REL32 else 0
    if kind == ADDR32NB:
        return program.base
    if kind in REL32S:
        return at + off + 4 + kind - 4
    return 0  # ADDR64: the address itself, all 8 bytes of it


def fill(code: bytearray, at: int, off: int, kind: int, address: int, program: Program) -> None:
    """A relocation at `off` of the code placed at `at`, filled in with the address it names, as the program's
    machine spells the kind: x86's DIR32 and AMD64's ADDR64 the address itself (ADDR64's 8 bytes), x86's REL32 and
    AMD64's REL32_n it relative to past the field, AMD64's ADDR32NB it relative to the image's base."""
    if program.x64 and kind == ADDR64:
        struct.pack_into("<Q", code, off, address)
    else:
        struct.pack_into("<I", code, off, (address - _anchored(program, kind, at, off)) & 0xFFFFFFFF)


def delinked(program: Program, obj: bytes) -> list[tuple[str, int, bytes]]:
    """The functions of a delinked object (a section each), placed back with their relocations: (name, address,
    code); an address of None: its symbol names no function of the project; code None: a relocation names nothing."""
    sections, symbols = read_coff(obj)
    owners = [next((s[0] for s in symbols if s and s[2] == i + 1 and not s[0].startswith("$")), "")
              for i in range(len(sections))]
    starts = [program.where.get(name) for name in owners]
    out = []
    for name, start, sec in zip(owners, starts, sections):
        code = bytearray(sec["data"] or b"")
        for off, index, kind in sec["relocs"] if start is not None else ():
            symbol, value, section, _ = symbols[index]
            target = (None if starts[section - 1] is None else starts[section - 1] + value) if section > 0 \
                else program.address(symbol)
            if target is None:
                code = None
                break
            fill(code, start, off, kind, target + struct.unpack_from("<i", code, off)[0], program)
        out.append((name, start, None if code is None else bytes(code)))
    return out


def from_c(program: Program, obj: bytes, extents: dict[str, int]) -> dict[str, bytes]:
    """The functions of a compiled object that are the game's code, by name, each as its code goes at its address:
    the function's own bytes (the padding after it is the program's), its relocations filled in. `extents`: every
    function's size, by name."""
    sections, symbols = read_coff(obj)
    mine = {}  # name -> (section number, start, end): each function up to the next one in its section
    for i, sec in enumerate(sections):
        defined = sorted((s[1], s[0]) for s in symbols if s and s[2] == i + 1 and s[3] & FUNCTION)
        for k, (value, name) in enumerate(defined):
            if name in extents:
                mine[name] = (i + 1, value, defined[k + 1][0] if k + 1 < len(defined) else sec["size"])

    def placed(section: int, offset: int) -> int | None:
        """The address a place in one of the compiled functions goes to (a jump table's label, a function)."""
        return next((program.where[n] + offset - lo for n, (s, lo, hi) in mine.items()
                     if s == section and lo <= offset < hi), None)

    out = {}
    for name, (section, lo, hi) in mine.items():
        sec, at, size = sections[section - 1], program.where[name], extents[name]
        relocs = [r for r in sec["relocs"] if lo <= r[0] < hi]
        body = sec["data"][lo:hi]
        end = max([len(body.rstrip(PADDING)), *(r[0] - lo + program.width(r[2]) for r in relocs)])
        if end > size or program.read(at + end, size - end).strip(PADDING):
            continue  # longer than the game's function, or shorter than its code
        code = bytearray(body[:end])
        for off, index, kind in relocs:
            symbol, value, where, _ = symbols[index]
            off -= lo
            if kind not in ((ADDR64, ADDR32NB, *REL32S) if program.x64 else (DIR32, REL32)):
                break
            # an AMD64 object keeps 0 in the relocatable field (observed of Visual C++'s and GNU's): a label is a
            # section-relative symbol, its value the offset, and nothing is inline
            inline = 0 if program.x64 else struct.unpack_from("<i", code, off)[0]
            target = program.address(symbol)
            if target is None and where > 0:
                target = placed(where, value + inline)
                if target is None:  # its own constant: used where the game's code reads it, if the bytes are there
                    word = int.from_bytes(program.read(at + off, program.width(kind)), "little", signed=True)
                    target = word + _anchored(program, kind, at, off)
                    if not _same_constant(program, sections[where - 1], symbols, where, value + inline, target):
                        break
                fill(code, at, off, kind, target, program)
            elif target is None:
                break  # a symbol the program has no address for
            else:
                fill(code, at, off, kind, target + inline, program)
        else:
            if bytes(code) == program.read(at, end):
                out[name] = bytes(code)
    return out


def _same_constant(program: Program, sec: dict, symbols: list, number: int, start: int, addr: int) -> bool:
    """Whether a constant of the compiled object (in section `sec`, number `number`, from `start` to the next symbol
    there) is the program's bytes at `addr`, its own relocations aside; uninitialised data can't say otherwise."""
    if sec["data"] is None:
        return True
    end = min([s[1] for s in symbols if s and s[2] == number and s[1] > start] + [sec["size"]])
    data = bytearray(sec["data"][start:end])
    game = bytearray(program.read(addr, len(data)).ljust(len(data), b"\0"))
    for off, _, kind in sec["relocs"]:
        for b in range(max(off, start), min(off + program.width(kind), end)):
            data[b - start] = game[b - start] = 0
    data = data.rstrip(b"\0")  # (what aligns the next constant)
    return bytes(game[:len(data)]) == bytes(data)


def _delink():
    """tools/delink.py, beside this: the project's programs and how a DLL is moved (rebase)."""
    spec = importlib.util.spec_from_file_location("delink", Path(__file__).with_name("delink.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rebuild(config: Path, build: Path, root: Path = Path(".")) -> dict:
    """Rebuild every program of the project (build/<program>.exe, and each DLL) and compare each with your copy:
    {"identical", "c" (the functions from C), "differ" (what doesn't come out as your copy's), "functions", "bytes"
    (of code), "size", "output"} for all of them (the .exe's output and size), and the same for each program, by its
    source files' folder ("" for the .exe), in "programs"."""
    info = json.loads((root / config).read_text(encoding="utf-8"))
    delink = _delink()
    each = {name: _rebuild_program(info, root, build, name, path, base, delink)
            for name, (path, _, base) in delink.programs(info).items()}
    report = {"identical": all(r["identical"] for r in each.values()),
              **{key: sorted(n for r in each.values() for n in r[key]) for key in ("c", "differ")},
              **{key: sum(r[key] for r in each.values()) for key in ("functions", "bytes")},
              "size": each[""]["size"], "output": each[""]["output"], "programs": each}
    (root / build / "rebuild.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def _rebuild_program(info: dict, root: Path, build: Path, folder: str, path: str, base: int | None, delink) -> dict:
    """One program rebuilt from its source files (a DLL's: those in its folder) and compared with your copy of it."""
    original = (root / path).read_bytes()
    exe = original if base is None else delink.rebase(original, base)  # (at the address the project gives it)
    program = Program(info, exe)
    sizes = {fn["name"]: fn["size"] for fns in info["units"].values() for fn in fns}
    out, placed, differ, c = bytearray(exe), [], [], []
    for unit in (u for u in info["units"] if delink.module(u) == folder):
        target = root / build / "target" / f"{unit}.obj"
        if not target.is_file():
            differ.append(f"{unit} (no object: the build didn't make it)")
            continue
        compiled = root / build / "src" / "auto" / f"{unit}.obj"
        own = from_c(program, compiled.read_bytes(), sizes) if compiled.is_file() else {}
        for name, start, code in delinked(program, target.read_bytes()):
            if start is None:
                differ.append(f"{name} (not in functions.json)")
                continue
            if code is None:
                differ.append(f"{name} (refers to a symbol that names no address)")
                continue
            off = program.offset(start)
            if off is None:  # (not in the program's file: uninitialised, or another program's address)
                differ.append(f"{name} (not in {Path(path).name})")
                continue
            if code != program.read(start, len(code)):
                differ.append(name)
            out[off:off + len(code)] = code
            if name in own:
                out[off:off + len(own[name])] = own[name]
                c.append(name)
            placed.append((start, len(code), name))
    placed.sort()
    differ += [f"{b[2]} (overlaps {a[2]})" for a, b in zip(placed, placed[1:]) if b[0] < a[0] + a[1]]
    where = Path(path)
    output = root / build / (where.relative_to("orig") if where.parts[0] == "orig" else where.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    if base is not None:  # back at its own address, as your copy is (a PE32+'s base is 8 bytes: _image_base)
        out = bytearray(delink.rebase(bytes(out), _image_base(original)))
    output.write_bytes(out)
    if out[XBE_MARK:XBE_MARK + 4] == b"XBEH":  # an Xbox game's program, made a Windows one: the XBE is inside it
        at, size = struct.unpack_from("<II", out, XBE_MARK + 4)
        output.with_name("default.xbe").write_bytes(out[at:at + size])
    return {"identical": not differ and out == original, "c": sorted(c),
            "differ": differ, "functions": len(placed), "bytes": sum(n for _, n, _ in placed), "size": len(out),
            "output": output.relative_to(root).as_posix()}


def _image_base(exe: bytes) -> int:
    """The address a program's header says it prefers: 8 bytes where a PE32+ keeps it, 4 where a PE32 does."""
    return struct.unpack_from("<Q" if _pe32plus(exe) else "<I", exe, _image_base_at(exe))[0]


def _image_base_at(exe: bytes) -> int:
    """Where a program's header says the address it prefers (its optional header's image base): PE32+ keeps it in 8
    bytes at its optional header's 24, a PE32 in 4 at 28."""
    at = struct.unpack_from("<I", exe, 0x3C)[0]
    return at + 24 + (PE32PLUS_IMAGE_BASE_AT if _pe32plus(exe) else PE32_IMAGE_BASE_AT)


def _pe32plus(exe: bytes) -> bool:
    """Whether a program is a PE32+ one (a 64-bit program, magic 0x20B): what its header's addresses depend on."""
    return struct.unpack_from("<H", exe, struct.unpack_from("<I", exe, 0x3C)[0] + 24)[0] == PE32PLUS_MAGIC


def main() -> None:
    report = rebuild(Path(sys.argv[1]), Path(sys.argv[2]))
    if report["identical"]:
        programs = ", ".join(r["output"] for r in report["programs"].values())
        print(f"identical: {programs} {'is' if len(report['programs']) == 1 else 'are'} your copy byte for byte "
              f"({len(report['c']):,} of {report['functions']:,} functions from C)")
        return
    shown = ", ".join(report["differ"][:SHOWN]) + ("…" if len(report["differ"]) > SHOWN else "")
    sys.exit(f"not identical: {len(report['differ'])} functions differ from your copy: {shown}")


if __name__ == "__main__":
    main()
