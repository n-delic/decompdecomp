"""Visual C++'s program database (.pdb): what the linker kept of a program's debug information, read for the import
(debuginfo.py). Both kinds Visual C++ wrote for 32-bit games: PDB 2.0 ("Microsoft C/C++ program database 2.00", Visual
C++ 4 to 6) and MSF 7.00 (Visual C++ .NET 2002 and every version since).

A PDB is a little file system (MSF) of numbered streams; the ones read here are Microsoft's own layout, as their
microsoft-pdb repository (cvinfo.h, dbi.h, tpi.h) and LLVM's documentation of it describe:
- stream 1, the PDB's identity: the signature (and from 7.00 a GUID) and age the program's CodeView record names, so a
  PDB is only used with the very build it belongs to;
- stream 3, DBI: the modules (one per object file linked, with its source files) and which bytes of the program each
  contributed: a function's module is its source file;
- each module's own stream: its functions (S_GPROC32, S_LPROC32) with their types, parameters and locals;
- the global symbols stream: public names (S_PUB32: the decorated names, as the linker saw them) and global variables
  with their types (S_GDATA32, S_LDATA32), and typedefs (S_UDT);
- stream 2, TPI: every type, by index from 0x1000.

Visual C++ before 2005 wrote names with a length byte before them (the _ST records, 0x1000-0x1012 symbols and
0x1000-0x1412 types); 2005 and later write them zero-terminated (0x1100- symbols, 0x1500- types), the records being
otherwise the same. Records this doesn't know are skipped; PDB 2.0's type records with 16-bit indices (Visual C++ 4)
aren't read: no 32-bit game of the period is known to ship one. Standard library only: no PDB reader on PyPI is both
pure Python and maintained (pdbparse, the one there is, needs construct 2.5 and stopped in 2020), and what's read here
is a few hundred lines.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MSF7 = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"
PDB2 = b"Microsoft C/C++ program database 2.00\r\n\x1aJG\0\0"
NIL = 0xFFFFFFFF  # a stream that isn't there
PDB_INFO, TPI, DBI = 1, 2, 3
PDB_VC70 = 20000404  # the PDB stream's version from which a GUID follows the age
DBI_HEADER_BYTES, DBI_VC70 = 64, 19990903  # source file names are zero-terminated from Visual C++ .NET
MODULE_BYTES = 64  # a module's record before its two names
SC_BYTES, SC_V2_BYTES = 28, 32  # a section contribution (version 2 adds the COFF section's index)
SC_V60, SC_V2 = 0xEFFE0000 + 19970605, 0xEFFE0000 + 20140516
SECTION_HEADER_BYTES = 40
CODE = 0x20  # IMAGE_SCN_CNT_CODE
SECTION_HEADERS = 5  # the optional debug header's stream index of the program's section headers
FIRST_TYPE = 0x1000

# symbol records (cvinfo.h): the _ST kinds (name after a length byte), then the same with a zero-terminated name
S_END = 0x0006
S_GPROC32_ST, S_LPROC32_ST, S_GPROC32, S_LPROC32 = 0x100B, 0x100A, 0x1110, 0x110F
S_BPREL32_ST, S_BPREL32 = 0x1006, 0x110B
S_REGREL32_ST, S_REGREL32 = 0x100D, 0x1111
S_REGISTER_ST, S_REGISTER = 0x1001, 0x1106
S_LDATA32_ST, S_GDATA32_ST, S_LDATA32, S_GDATA32 = 0x1007, 0x1008, 0x110C, 0x110D
S_PUB32_ST, S_PUB32 = 0x1009, 0x110E
S_UDT_ST, S_UDT = 0x1003, 0x1108
S_PROCREF_ST, S_LPROCREF_ST = 0x0400, 0x0403  # PDB 2.0 keeps their names after the record, not in it
ST_SYMBOLS = {S_GPROC32_ST, S_LPROC32_ST, S_BPREL32_ST, S_REGREL32_ST, S_REGISTER_ST, S_LDATA32_ST, S_GDATA32_ST,
              S_PUB32_ST, S_UDT_ST}
PROCS = {S_GPROC32_ST, S_LPROC32_ST, S_GPROC32, S_LPROC32}
PUBLIC_FUNCTION = 2  # S_PUB32's flags (2005 on): code
REGISTERS = {17: "eax", 18: "ecx", 19: "edx", 20: "ebx", 21: "esp", 22: "ebp", 23: "esi", 24: "edi"}  # CV_REG_*

# type records (cvinfo.h): (_ST kind, kind from 2005)
LF_MODIFIER, LF_POINTER, LF_PROCEDURE, LF_MFUNCTION, LF_ARGLIST, LF_FIELDLIST, LF_BITFIELD = (
    0x1001, 0x1002, 0x1008, 0x1009, 0x1201, 0x1203, 0x1205)
LF_ARRAY, LF_CLASS, LF_STRUCTURE, LF_UNION, LF_ENUM = (0x1003, 0x1503), (0x1004, 0x1504), (0x1005, 0x1505), \
    (0x1006, 0x1506), (0x1007, 0x1507)
LF_BCLASS, LF_VBCLASS, LF_IVBCLASS, LF_INDEX, LF_VFUNCTAB, LF_FRIENDCLS, LF_VFUNCOFF = (
    0x1400, 0x1401, 0x1402, 0x1404, 0x1409, 0x140A, 0x140C)
LF_ENUMERATE, LF_FRIENDFCN, LF_MEMBER, LF_STMEMBER, LF_METHOD, LF_NESTTYPE, LF_ONEMETHOD, LF_NESTTYPEEX, \
    LF_MEMBERMODIFY = (0x0403, 0x1502), (0x1403, 0x150C), (0x1405, 0x150D), (0x1406, 0x150E), (0x1407, 0x150F), \
    (0x1408, 0x1510), (0x140B, 0x1511), (0x140D, 0x1512), (0x140E, 0x1513)
ST_TYPES = {k[0] for k in (LF_ARRAY, LF_CLASS, LF_STRUCTURE, LF_UNION, LF_ENUM, LF_ENUMERATE, LF_FRIENDFCN,
                           LF_MEMBER, LF_STMEMBER, LF_METHOD, LF_NESTTYPE, LF_ONEMETHOD, LF_NESTTYPEEX,
                           LF_MEMBERMODIFY)}
FORWARD, UNIQUE_NAME = 0x80, 0x200  # a struct's properties: only declared here; a decorated name follows its name
INTRODUCING_VIRTUAL = (4, 6)  # a method's property (attr >> 2 & 7) after which its vtable offset follows
# numeric leaves: a value of 0x8000 or more says which kind of number follows
NUMERIC = {0x8000: "<b", 0x8001: "<h", 0x8002: "<H", 0x8003: "<i", 0x8004: "<I", 0x8009: "<q", 0x800A: "<Q"}


class PdbError(ValueError):
    pass


def streams(data: bytes) -> list[bytes]:
    """Every stream of the PDB, by number ("" for one that isn't there)."""
    if data.startswith(MSF7):
        size, _, _, directory_bytes, _, map_at = struct.unpack_from("<6I", data, len(MSF7))
        index, at = "<I", 4
        blocks = list(struct.unpack_from(f"<{-(-directory_bytes // size)}I", data, map_at * size))
    elif data.startswith(PDB2):
        size, _, _, directory_bytes = struct.unpack_from("<IHHI", data, len(PDB2))
        index, at = "<H", 2
        blocks = list(struct.unpack_from(f"<{-(-directory_bytes // size)}H", data, len(PDB2) + 16))
    else:
        raise PdbError("not a Visual C++ program database")

    def read(pages, n: int) -> bytes:
        out = b"".join(data[p * size:(p + 1) * size] for p in pages)[:n]
        if len(out) < n:
            raise PdbError("the program database is cut short")
        return out
    directory = read(blocks, directory_bytes)
    count = struct.unpack_from(index, directory)[0]
    if data.startswith(MSF7):  # a count, the sizes, then each stream's blocks
        sizes = struct.unpack_from(f"<{count}I", directory, 4)
        at = 4 + 4 * count
    else:  # a count and a reserved word, (size, reserved) pairs, then each stream's pages
        sizes = [struct.unpack_from("<I", directory, 4 + 8 * i)[0] for i in range(count)]
        at = 4 + 8 * count
    width = struct.calcsize(index)
    out = []
    for n in sizes:
        if n == NIL:
            out.append(b"")
            continue
        k = -(-n // size)
        out.append(read(struct.unpack_from(f"<{k}{index[1]}", directory, at), n))
        at += width * k
    return out


def name_at(data: bytes, at: int, st: bool) -> tuple[str, int]:
    """A record's name at `at`, and where it ends: after a length byte (st), or zero-terminated."""
    if st:
        n = data[at] if at < len(data) else 0
        return data[at + 1:at + 1 + n].decode("latin-1"), at + 1 + n
    end = data.find(b"\0", at)
    end = len(data) if end < 0 else end
    return data[at:end].decode("latin-1"), end + 1


def numeric(data: bytes, at: int) -> tuple[int, int]:
    value = struct.unpack_from("<H", data, at)[0]
    if value < 0x8000:
        return value, at + 2
    fmt = NUMERIC.get(value)
    if fmt is None:
        raise PdbError(f"a number of kind {value:#x}")
    return struct.unpack_from(fmt, data, at + 2)[0], at + 2 + struct.calcsize(fmt)


def records(data: bytes, at: int = 0, end: int | None = None):
    """(kind, body) of each symbol or type record: a 16-bit length, then the kind and the body."""
    end = len(data) if end is None else end
    while at + 4 <= end:
        length, kind = struct.unpack_from("<HH", data, at)
        if length < 2:
            return
        yield kind, data[at + 4:at + 2 + length]
        at += 2 + length
        if kind in (S_PROCREF_ST, S_LPROCREF_ST) and at < end and length == 14:  # PDB 2.0: its name, padded to 4
            at = (at + 1 + data[at] + 3) & ~3


@dataclass
class Variable:
    name: str
    type: int
    where: str  # "ebp+0x8", "ecx", "esp+0x10"
    param: bool


@dataclass
class Procedure:
    name: str  # as the compiler wrote it: entity_spawn, Town::Update
    section: int
    offset: int
    size: int
    type: int
    module: int
    variables: list[Variable] = field(default_factory=list)


@dataclass
class Datum:
    name: str
    section: int
    offset: int
    type: int  # 0: not known (a public)
    function: bool = False


@dataclass
class Module:
    obj: str  # the object file, as the linker saw it
    library: str  # the object or library it came from
    stream: int
    symbols_bytes: int
    sources: list[str] = field(default_factory=list)


@dataclass
class Pdb:
    signature: int
    age: int
    guid: bytes  # 16 bytes from MSF 7.00; b"" before
    kind: str  # "PDB 2.0" or "MSF 7.00"
    modules: list[Module] = field(default_factory=list)
    contributions: list[tuple[int, int, int, int]] = field(default_factory=list)  # (section, offset, size, module)
    sections: list[tuple[int, int]] = field(default_factory=list)  # (rva, characteristics) by number - 1
    procedures: list[Procedure] = field(default_factory=list)
    publics: list[Datum] = field(default_factory=list)
    globals: list[Datum] = field(default_factory=list)
    typedefs: list[tuple[str, int]] = field(default_factory=list)  # S_UDT: (name, type)
    types: dict[int, tuple[int, bytes]] = field(default_factory=dict)  # index -> (kind, body)
    dbi_age: int = 0

    def rva(self, section: int, offset: int) -> int | None:
        return self.sections[section - 1][0] + offset if 0 < section <= len(self.sections) else None

    def code(self, section: int) -> bool:
        return 0 < section <= len(self.sections) and bool(self.sections[section - 1][1] & CODE)


def read(data: bytes) -> Pdb:
    """The parts of a PDB the import uses (a PdbError when it isn't one, or is broken)."""
    try:
        return _read(streams(data))
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise PdbError(f"the program database is broken ({e})") from None


def _read(found: list[bytes]) -> Pdb:
    info = found[PDB_INFO]
    version, signature, age = struct.unpack_from("<3I", info)
    pdb = Pdb(signature, age, info[12:28] if version >= PDB_VC70 else b"",
              "MSF 7.00" if version >= PDB_VC70 else "PDB 2.0")
    _read_types(pdb, found[TPI])
    dbi = found[DBI]
    if len(dbi) < DBI_HEADER_BYTES:
        return pdb
    (_, dbi_version, pdb.dbi_age, _, _, _, _, records_stream, _, modules_bytes, contributions_bytes, map_bytes,
     sources_bytes, server_bytes, _, debug_bytes, ec_bytes) = struct.unpack_from("<iIIHHHHHHiiiiiIii", dbi)
    at = DBI_HEADER_BYTES
    end = at + modules_bytes
    while at + MODULE_BYTES <= end:
        stream, symbols_bytes = struct.unpack_from("<HI", dbi, at + 34)
        obj, after = name_at(dbi, at + MODULE_BYTES, False)
        library, after = name_at(dbi, after, False)
        pdb.modules.append(Module(obj, library, stream, symbols_bytes))
        at = (after + 3) & ~3
    contributions = dbi[end:end + contributions_bytes]
    width = SC_V2_BYTES if contributions[:4] == struct.pack("<I", SC_V2) else SC_BYTES
    for at in range(4, len(contributions) - width + 1, width):
        section, _, offset, size, _, module = struct.unpack_from("<HHiiIH", contributions, at)
        pdb.contributions.append((section, offset, size, module))
    at = end + contributions_bytes + map_bytes
    _read_sources(pdb, dbi[at:at + sources_bytes], dbi_version >= DBI_VC70)
    at += sources_bytes + server_bytes + ec_bytes
    debug = dbi[at:at + debug_bytes]
    if len(debug) >= 2 * (SECTION_HEADERS + 1):
        stream = struct.unpack_from("<H", debug, 2 * SECTION_HEADERS)[0]
        headers = found[stream] if stream < len(found) else b""
        for at in range(0, len(headers) - SECTION_HEADER_BYTES + 1, SECTION_HEADER_BYTES):
            rva = struct.unpack_from("<I", headers, at + 12)[0]
            pdb.sections.append((rva, struct.unpack_from("<I", headers, at + 36)[0]))
    for number, module in enumerate(pdb.modules):
        if module.stream < len(found) and module.symbols_bytes:
            _read_module(pdb, found[module.stream], number, module.symbols_bytes)
    if records_stream < len(found):
        _read_globals(pdb, found[records_stream])
    return pdb


def _read_sources(pdb: Pdb, data: bytes, zero_terminated: bool) -> None:
    """Each module's source files (headers too), from DBI's file info."""
    if len(data) < 4:
        return
    modules = struct.unpack_from("<H", data)[0]
    counts = struct.unpack_from(f"<{modules}H", data, 4 + 2 * modules)
    total = sum(counts)  # (its own count of names is 16 bits, and a big game has more)
    offsets = struct.unpack_from(f"<{total}I", data, 4 + 4 * modules)
    names = 4 + 4 * modules + 4 * total
    k = 0
    for module, count in zip(pdb.modules, counts):
        module.sources = [name_at(data, names + offsets[k + i], not zero_terminated)[0] for i in range(count)]
        k += count


def _read_module(pdb: Pdb, data: bytes, number: int, size: int) -> None:
    current = None
    depth = 0
    for kind, body in records(data, 4, size):
        st = kind in ST_SYMBOLS
        if kind in PROCS:
            length, _, _, type_index, offset, section = struct.unpack_from("<IIIIIH", body, 12)
            name = name_at(body, 35, st)[0]
            current = Procedure(name, section, offset, length, type_index, number)
            pdb.procedures.append(current)
            depth = 1
        elif kind == S_END or kind in (0x1103, 0x0207):  # the end of a procedure or block; S_BLOCK32 opens one
            depth += 1 if kind != S_END else -1
            if depth <= 0:
                current = None
        elif current is not None and kind in (S_BPREL32_ST, S_BPREL32):
            offset, type_index = struct.unpack_from("<iI", body)
            name = name_at(body, 8, st)[0]
            current.variables.append(Variable(name, type_index, f"stack{offset:+#x}", offset > 0 and depth == 1))
        elif current is not None and kind in (S_REGREL32_ST, S_REGREL32):
            offset, type_index, register = struct.unpack_from("<iIH", body)
            name = name_at(body, 10, st)[0]
            base = REGISTERS.get(register, f"r{register}")
            current.variables.append(Variable(name, type_index, f"{base}{offset:+#x}",
                                              offset > 0 and base in ("ebp", "esp") and depth == 1))
        elif current is not None and kind in (S_REGISTER_ST, S_REGISTER):
            type_index, register = struct.unpack_from("<IH", body)
            name = name_at(body, 6, st)[0]
            current.variables.append(Variable(name, type_index, REGISTERS.get(register, f"r{register}"), False))
        elif kind in (S_LDATA32_ST, S_GDATA32_ST, S_LDATA32, S_GDATA32) and current is None:
            type_index, offset, section = struct.unpack_from("<IIH", body)
            pdb.globals.append(Datum(name_at(body, 10, st)[0], section, offset, type_index))


def _read_globals(pdb: Pdb, data: bytes) -> None:
    seen = {(d.section, d.offset) for d in pdb.globals}
    for kind, body in records(data):
        st = kind in ST_SYMBOLS
        if kind in (S_PUB32_ST, S_PUB32):
            flags, offset, section = struct.unpack_from("<IIH", body)
            pdb.publics.append(Datum(name_at(body, 10, st)[0], section, offset, 0,
                                     kind == S_PUB32 and bool(flags & PUBLIC_FUNCTION)))
        elif kind in (S_GDATA32_ST, S_LDATA32_ST, S_GDATA32, S_LDATA32):
            type_index, offset, section = struct.unpack_from("<IIH", body)
            if (section, offset) not in seen:
                seen.add((section, offset))
                pdb.globals.append(Datum(name_at(body, 10, st)[0], section, offset, type_index))
        elif kind in (S_UDT_ST, S_UDT):
            pdb.typedefs.append((name_at(body, 4, st)[0], struct.unpack_from("<I", body)[0]))


def _read_types(pdb: Pdb, data: bytes) -> None:
    if len(data) < 20:
        return
    _, header_bytes, first, last = struct.unpack_from("<4I", data)
    index = first
    for kind, body in records(data, header_bytes):
        pdb.types[index] = (kind, body)
        index += 1
        if index >= last:
            break
