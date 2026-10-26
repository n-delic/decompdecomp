"""What a Windows program's own debug information gives its import, when the game shipped with it or the user has it:
the names of its functions and data, the source file each function was compiled from, and, from a program database,
each function's prototype with its parameters' and locals' names and every struct, class, union, enum and typedef the
game used, written as C for the project's header (through the PS2's cheader.py, which writes STABS' types).

Three places a Visual C++ program's symbols can be, each read here:
- a program database (.pdb, pdb.py) beside the program: used only when it's the one the program names (the signature
  and age, and from Visual C++ .NET the GUID, of the program's CodeView record), since another build's has other
  addresses;
- the linker's map (.map, /MAP) beside the program: every public symbol by address, functions marked, each with the
  object file it came from; used when its timestamp is the program's;
- COFF symbols in the program itself (/DEBUGTYPE:COFF, and other linkers' -g): names, and the source file of each
  run of them where there are .file records (GNU's linker writes them, Microsoft's doesn't).

The import (scratch.create) gives functions.json's "names" every name a project can use as a real name (a C name, or
a method's Class::name: names.py pairs either with the game's symbol), makes each object file a source file of the
project (src/auto/<its name>.cpp), writes the types into include/debug_types.h and keeps what briefs say about each
function in config/<version>/debug.json (WindowsPC.debug_of).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..ps2 import cheader, stabs, symtab
from ..ps2.debuginfo import DebugInfo
from . import pdb as pdbfile

# a real name a project can give a function or a datum: names.py pairs a C name, a method's Class::name and a
# constructor's or destructor's (Class::Class, Class::~Class) with the game's symbol, and nothing else (operators,
# templates)
REAL_NAME = re.compile(r"[A-Za-z_]\w*(?:::~?[A-Za-z_]\w*)*")
_C_SYMBOL = re.compile(r"[_@]?([A-Za-z_$][\w$]*?)(?:@\d+)?")  # _f, _f@8 (__stdcall), @f@8 (__fastcall), f
SOURCE_SUFFIXES = (".c", ".cpp", ".cxx", ".cc")
ANONYMOUS = re.compile(r"<unnamed|__unnamed|<anonymous")  # the compiler's names for a type without one
CODEVIEW, DEBUG_DIRECTORY_AT, DEBUG_ENTRY_BYTES = 2, 96 + 6 * 8, 28  # IMAGE_DEBUG_TYPE_CODEVIEW; data directory 6
COFF_SYMBOL_BYTES, C_EXTERNAL, C_STATIC, C_FILE, FUNCTION_TYPE = 18, 2, 3, 103, 0x20
POINTER_BYTES = 4
CALLS = {0x07: "__stdcall", 0x04: "__fastcall"}  # CV_call_e: what a prototype says beyond __cdecl and __thiscall
# CodeView's basic types (the low byte of a type index below 0x1000): (name as stabs/cheader spell it, bytes, signed)
BASIC = {0x03: ("void", 0, True), 0x10: ("signed char", 1, True), 0x20: ("unsigned char", 1, False),
         0x70: ("char", 1, True), 0x68: ("signed char", 1, True), 0x69: ("unsigned char", 1, False),
         0x11: ("short int", 2, True), 0x21: ("short unsigned int", 2, False), 0x72: ("short int", 2, True),
         0x73: ("short unsigned int", 2, False), 0x12: ("long int", 4, True), 0x22: ("long unsigned int", 4, False),
         0x74: ("int", 4, True), 0x75: ("unsigned int", 4, False), 0x13: ("__int64", 8, True),
         0x23: ("unsigned __int64", 8, False), 0x76: ("__int64", 8, True), 0x77: ("unsigned __int64", 8, False),
         0x40: ("float", 4, True), 0x41: ("double", 8, True), 0x42: ("double", 8, True),
         0x71: ("wchar_t", 2, False), 0x30: ("bool", 1, False), 0x08: ("long int", 4, True)}


@dataclass
class Found:
    """The debug information found for a program, and what the import log says about where it came from."""
    info: DebugInfo
    source: str  # "game.pdb (PDB 2.0)", "game.map", "the program's COFF symbols"


def codeview(data: bytes) -> tuple[str, bytes, int, int, str] | None:
    """The program's CodeView record: (kind "NB10" or "RSDS", GUID or b"", signature, age, the PDB's path), or None."""
    from .scratch import OPTIONAL_HEADER_AT, _pe_header, read_pe

    at = _pe_header(data)
    if at is None:
        return None
    opt = at + OPTIONAL_HEADER_AT
    rva, size = struct.unpack_from("<II", data, opt + DEBUG_DIRECTORY_AT)
    pe = read_pe(data)
    for k in range(size // DEBUG_ENTRY_BYTES if rva else 0):
        entry = pe.read(pe.image_base + rva + DEBUG_ENTRY_BYTES * k, DEBUG_ENTRY_BYTES)
        if len(entry) < DEBUG_ENTRY_BYTES or struct.unpack_from("<I", entry, 12)[0] != CODEVIEW:
            continue
        length, _, offset = struct.unpack_from("<III", entry, 16)
        record = data[offset:offset + length]
        if record[:4] == b"NB10":
            signature, age = struct.unpack_from("<II", record, 8)
            return "NB10", b"", signature, age, record[16:].split(b"\0")[0].decode("latin-1")
        if record[:4] == b"RSDS":
            return "RSDS", record[4:20], 0, struct.unpack_from("<I", record, 20)[0], \
                record[24:].split(b"\0")[0].decode("latin-1")
    return None


def plain_name(symbol: str) -> str | None:
    """The name a decorated symbol stands for, as a project's real name: _f, _f@8, @f@8 -> f; ?Update@Town@@QAEXH@Z
    -> Town::Update; ??0CFoo@@QAE@XZ -> CFoo::CFoo. None for what no real name pairs with (operators, templates,
    the compiler's own: strings, floating-point constants, imports)."""
    if symbol.startswith("??0") or symbol.startswith("??1"):
        scopes = symbol[3:].split("@@", 1)[0].split("@")[::-1]
        name = "::".join(scopes) + "::" + "~" * (symbol[2] == "1") + scopes[-1]
    elif symbol.startswith("??") or symbol.startswith("__imp_"):
        return None
    elif symbol.startswith("?"):
        name = "::".join(symbol[1:].split("@@", 1)[0].split("@")[::-1])
    else:
        m = _C_SYMBOL.fullmatch(symbol)
        name = m.group(1) if m else ""
    return name if REAL_NAME.fullmatch(name) else None


def _key(name: str, used: set[str], suffix: int) -> str:
    """A name not used yet: a second static `count` of another file becomes count_00403010."""
    if name in used:
        name = f"{name}_{suffix:08X}"
    used.add(name)
    return name


def _source_of(module: pdbfile.Module) -> str:
    """A module's source file: its own C or C++ file (the one named like its object first), else its object."""
    stem = PureWindowsPath(module.obj).stem.lower()
    own = [s for s in module.sources if PureWindowsPath(s).suffix.lower() in SOURCE_SUFFIXES]
    best = next((s for s in own if PureWindowsPath(s).stem.lower() == stem), own[0] if own else module.obj)
    return best.replace("\\", "/")


# ------------------------------------------------------------------------------------------------------ the PDB


class _Types:
    """The PDB's types as stabs.Type, which cheader.Header writes as C: each read once, by index."""

    def __init__(self, found: pdbfile.Pdb):
        self.pdb = found
        self.done: dict[int, stabs.Type] = {}
        defined: dict[str, int] = {}  # a struct's name -> the index of its definition
        forward: dict[int, str] = {}  # a forward reference's index -> the name it refers to
        for index, (kind, body) in found.types.items():
            if kind in (*pdbfile.LF_CLASS, *pdbfile.LF_STRUCTURE, *pdbfile.LF_UNION, *pdbfile.LF_ENUM):
                name, properties = self._name(kind, body)
                if properties & pdbfile.FORWARD:
                    forward[index] = name
                else:
                    defined.setdefault(name, index)
        # a type referred to before it's defined is its definition, wherever it's referred to from
        self.definition = {index: defined[name] for index, name in forward.items() if name in defined}

    @staticmethod
    def _name(kind: int, body: bytes) -> tuple[str, int]:
        properties = struct.unpack_from("<H", body, 2)[0]
        # (a struct's or class's size follows its field list, derived classes and vtable shape; a union's, its field
        # list; an enum's name, its underlying type and field list)
        at = 12 if kind in pdbfile.LF_ENUM else pdbfile.numeric(body, 8 if kind in pdbfile.LF_UNION else 16)[1]
        return pdbfile.name_at(body, at, kind in pdbfile.ST_TYPES)[0], properties

    def get(self, index: int) -> stabs.Type:
        index = self.definition.get(index, index)
        if index in self.done:
            return self.done[index]
        t = stabs.Type()
        self.done[index] = t  # (before reading it: a struct's fields can point back at it)
        try:
            self._fill(t, index)
        except (struct.error, IndexError, pdbfile.PdbError):
            t.kind = "unknown"
        return t

    def _fill(self, t: stabs.Type, index: int) -> None:
        if index < pdbfile.FIRST_TYPE:
            mode, basic = index >> 8 & 0xF, index & 0xFF
            if mode:
                t.kind, t.size, t.target = "pointer", POINTER_BYTES, self.get(basic)
                return
            name, size, signed = BASIC.get(basic, ("int", 4, True))
            t.kind = "void" if basic == 0x03 or basic == 0 else "float" if basic in (0x40, 0x41, 0x42) else "base"
            t.name, t.size, t.signed = name, size, signed
            return
        if index not in self.pdb.types:
            t.kind = "unknown"
            return
        kind, body = self.pdb.types[index]
        st = kind in pdbfile.ST_TYPES
        if kind == pdbfile.LF_MODIFIER:
            target, attributes = struct.unpack_from("<IH", body)
            t.kind = "const" if attributes & 1 else "volatile" if attributes & 2 else "alias"
            t.target = self.get(target)
        elif kind == pdbfile.LF_POINTER:
            target = struct.unpack_from("<I", body)[0]
            t.kind, t.size, t.target = "pointer", POINTER_BYTES, self.get(target)  # (a reference too: C has none)
        elif kind in pdbfile.LF_ARRAY:
            element = struct.unpack_from("<I", body)[0]
            size = pdbfile.numeric(body, 8)[0]
            t.kind, t.target = "array", self.get(element)
            each = self.size(t.target)
            t.count, t.size = (size // each if each else 0), size
        elif kind in (pdbfile.LF_PROCEDURE, pdbfile.LF_MFUNCTION):
            t.kind, t.target = "function", self.get(struct.unpack_from("<I", body)[0])
        elif kind in (*pdbfile.LF_CLASS, *pdbfile.LF_STRUCTURE, *pdbfile.LF_UNION):
            union = kind in pdbfile.LF_UNION
            _, properties, fields = struct.unpack_from("<HHI", body)
            size, at = pdbfile.numeric(body, 8 if union else 16)
            name = pdbfile.name_at(body, at, st)[0]
            if properties & pdbfile.FORWARD:  # (one defined nowhere: get() takes the others to their definition)
                t.kind, t.xref, t.name = "xref", "union" if union else "struct", name
                return
            t.kind, t.size = ("union" if union else "struct"), size
            t.tag = "" if ANONYMOUS.search(name) else name
            self._fields(t, fields)
        elif kind in pdbfile.LF_ENUM:
            _, properties, underlying, fields = struct.unpack_from("<HHII", body)
            name = pdbfile.name_at(body, 12, st)[0]
            t.kind, t.size = "enum", self.size(self.get(underlying)) or 4
            t.tag = "" if ANONYMOUS.search(name) else name
            if not properties & pdbfile.FORWARD:
                self._fields(t, fields)
        elif kind == pdbfile.LF_BITFIELD:  # (a member's: _fields reads it)
            t.kind, t.target = "alias", self.get(struct.unpack_from("<I", body)[0])
        else:  # the vtable's shape, a method list...: nothing a C declaration has
            t.kind, t.size = "unknown", 0

    def size(self, t: stabs.Type) -> int:
        for _ in range(64):
            if t.kind in ("alias", "const", "volatile") and t.target is not None:
                t = t.target
            else:
                break
        return t.size

    def _fields(self, t: stabs.Type, index: int) -> None:
        """A field list's members, base classes, vtable pointer and enumerators into `t`."""
        for _ in range(4096):  # a long list continues in another (LF_INDEX)
            if index not in self.pdb.types or self.pdb.types[index][0] != pdbfile.LF_FIELDLIST:
                return
            data, at, index = self.pdb.types[index][1], 0, 0
            while at + 2 <= len(data):
                if data[at] >= 0xF0:  # padding to 4 bytes
                    at += data[at] & 0x0F
                    continue
                leaf = struct.unpack_from("<H", data, at)[0]
                st = leaf in pdbfile.ST_TYPES
                if leaf in pdbfile.LF_MEMBER:
                    member = struct.unpack_from("<I", data, at + 4)[0]
                    offset, at = pdbfile.numeric(data, at + 8)
                    name, at = pdbfile.name_at(data, at, st)
                    t.fields.append(self._member(name, member, offset))
                elif leaf in pdbfile.LF_ENUMERATE:
                    value, at = pdbfile.numeric(data, at + 4)
                    name, at = pdbfile.name_at(data, at, st)
                    t.enumerators.append((name, value))
                elif leaf == pdbfile.LF_BCLASS:
                    base = struct.unpack_from("<I", data, at + 4)[0]
                    offset, at = pdbfile.numeric(data, at + 8)
                    t.bases.append((offset * stabs.BITS_PER_BYTE, self.get(base)))
                elif leaf in (pdbfile.LF_VBCLASS, pdbfile.LF_IVBCLASS):  # a virtual base: where it is is found at run time
                    at = pdbfile.numeric(data, pdbfile.numeric(data, at + 12)[1])[1]
                elif leaf == pdbfile.LF_VFUNCTAB:
                    t.fields.append(stabs.Field("vftable", stabs.Type("pointer", size=POINTER_BYTES,
                                                                      target=stabs.Type("void")), 0, 0))
                    at += 8
                elif leaf == pdbfile.LF_INDEX:
                    index = struct.unpack_from("<I", data, at + 4)[0]
                    at += 8
                elif leaf in pdbfile.LF_ONEMETHOD:
                    attributes = struct.unpack_from("<H", data, at + 2)[0]
                    at += 8 + 4 * (attributes >> 2 & 7 in pdbfile.INTRODUCING_VIRTUAL)
                    at = pdbfile.name_at(data, at, st)[1]
                elif leaf in (*pdbfile.LF_METHOD, *pdbfile.LF_NESTTYPE, *pdbfile.LF_FRIENDFCN, *pdbfile.LF_NESTTYPEEX,
                              *pdbfile.LF_MEMBERMODIFY, *pdbfile.LF_STMEMBER):
                    at = pdbfile.name_at(data, at + 8, st)[1]
                elif leaf == pdbfile.LF_FRIENDCLS:
                    at += 8
                elif leaf == pdbfile.LF_VFUNCOFF:
                    at += 12
                else:  # a member this doesn't know: its length isn't known either, so the rest of the list is lost
                    break
            if not index:
                return

    def _member(self, name: str, index: int, offset: int) -> stabs.Field:
        kind, body = self.pdb.types.get(index, (0, b""))
        if kind == pdbfile.LF_BITFIELD:
            underlying, length, position = struct.unpack_from("<IBB", body)
            return stabs.Field(name, self.get(underlying), offset * stabs.BITS_PER_BYTE + position, length)
        return stabs.Field(name, self.get(index), offset * stabs.BITS_PER_BYTE, 0)

    def signature(self, index: int) -> tuple[stabs.Type, list[stabs.Type], str]:
        """A function type's return type, parameters' types and calling convention (what a prototype says)."""
        kind, body = self.pdb.types.get(index, (0, b""))
        if kind == pdbfile.LF_PROCEDURE:
            returns, call, _, _, arguments = struct.unpack_from("<IBBHI", body)
        elif kind == pdbfile.LF_MFUNCTION:
            returns, _, _, call, _, _, arguments = struct.unpack_from("<IIIBBHI", body)
        else:
            return stabs.Type(kind="base", size=4, name="int"), [], ""
        args = []
        if self.pdb.types.get(arguments, (0,))[0] == pdbfile.LF_ARGLIST:
            data = self.pdb.types[arguments][1]
            count = struct.unpack_from("<I", data)[0]
            args = [self.get(a) for a in struct.unpack_from(f"<{count}I", data, 4) if a]
            if len(args) == 1 and args[0].kind == "void":
                args = []
        return self.get(returns), args, CALLS.get(call, "")


class _Header(cheader.Header):
    """cheader's C, spelled as Visual C++ 6.0 and later compile it: 64-bit integers are __int64 (6.0 has no long
    long), and bool is C++'s (the project's sources are C++)."""

    def _specifier(self, t: stabs.Type, deps: set, by_value: bool) -> str:
        if t.kind == "base" and t.size == 8:
            return "__int64" if t.signed else "unsigned __int64"
        if t.kind == "base" and t.name in ("bool", "wchar_t"):
            return t.name
        return super()._specifier(t, deps, by_value)


def from_pdb(found: pdbfile.Pdb, image_base: int, reserved: set[str]) -> DebugInfo:
    """Names, source files, prototypes and types from a program database."""
    types = _Types(found)
    info = DebugInfo()
    used: set[str] = set()

    def address(section: int, offset: int) -> int | None:
        rva = found.rva(section, offset)
        return None if rva is None else image_base + rva
    # (not "* Linker *", which has no object: the linker's own bits, the import table)
    sources = {number: _source_of(m) for number, m in enumerate(found.modules) if m.library}
    contributions = sorted((a, a + size, module) for section, offset, size, module in found.contributions
                           if (a := address(section, offset)) is not None and size > 0 and found.code(section))
    procedures = {}
    for p in found.procedures:
        at = address(p.section, p.offset)
        if at is not None and at not in procedures:
            procedures[at] = p
    for at, p in sorted(procedures.items()):
        if (name := plain_name(p.name) if "@" in p.name else p.name) and REAL_NAME.fullmatch(name):
            info.named.append(symtab.Named(_key(name, used, at), at, p.size, True, ".text"))
        if p.module in sources:
            info.units[at] = sources[p.module]
    named = {n.address for n in info.named}
    for d in found.globals:
        at = address(d.section, d.offset)
        if at is not None and at not in named and REAL_NAME.fullmatch(d.name):
            named.add(at)
            info.named.append(symtab.Named(_key(d.name, used, at), at, 0, False, ".data"))
    for d in found.publics:  # what the module streams don't say (code without debug information: libraries)
        at = address(d.section, d.offset)
        name = plain_name(d.name)
        if at is not None and at not in named and name:
            named.add(at)
            code = d.function or found.code(d.section)
            info.named.append(symtab.Named(_key(name, used, at), at, 0, code, ".text" if code else ".data"))
    for lo, _, module in contributions:  # a function the module streams don't name still has its object
        info.units.setdefault(lo, sources.get(module, ""))
    info.units = {k: v for k, v in info.units.items() if v}
    info.sources = sorted({sources[p.module] for p in procedures.values() if p.module in sources})
    _types_and_prototypes(info, found, types, procedures, address, reserved)
    info.named.sort(key=lambda n: n.address)
    return info


def _types_and_prototypes(info: DebugInfo, found: pdbfile.Pdb, types: _Types, procedures: dict, address,
                          reserved: set[str]) -> None:
    source = stabs.SourceInfo("program database")
    for index, (kind, body) in found.types.items():  # every type the program defines, in the PDB's order
        if kind in (*pdbfile.LF_CLASS, *pdbfile.LF_STRUCTURE, *pdbfile.LF_UNION, *pdbfile.LF_ENUM):
            t = types.get(index)
            if t.kind in cheader.TAGGED and t.tag and t not in source.types:
                source.types.append(t)
    for name, index in found.typedefs:
        target = types.get(index)
        if target.kind in cheader.TAGGED and target.tag == name:
            target.typedef = name  # a C++ class is a type name of its own
        elif name and not ANONYMOUS.search(name):
            source.types.append(stabs.Type("alias", typedef=name, target=target))
    header = _Header([source], reserved)
    info.types = len(header.entities)
    names = {n.address: n.name for n in info.named}
    declared = []  # the game's own free functions, declared after its types: C++ calls only what's declared
    for at, p in procedures.items():
        returns, params, call = types.signature(p.type)
        # __fastcall's first two parameters come in ecx and edx, which the PDB lists as registers
        given = ([v for v in p.variables if v.where in ("ecx", "edx")][:2] if call == "__fastcall" else []) + \
            [v for v in p.variables if v.param]
        given = given[:len(params)]
        args = [stabs.Variable(given[k].name if k < len(given) else f"arg{k}", t, given[k].where if k < len(given)
                               else "") for k, t in enumerate(params)]
        function = stabs.Function(p.name, at, returns, False, p.size, args,
                                  [stabs.Variable(v.name, types.get(v.type), v.where) for v in p.variables
                                   if v not in given])
        name = names.get(at, f"func_{at:08X}")
        info.functions[at] = {
            "source": info.units.get(at, ""),
            "prototype": header.prototype(function, f"{call} {name}" if call else name),
            "params": [f"{v.name} in {v.where}" for v in args if v.where],
            "locals": [f"{header.variable(v)} ({v.where})" for v in function.locals],
        }
        module = found.modules[p.module] if p.module < len(found.modules) else None
        if at in names and "::" not in name and module is not None and module.library == module.obj:
            declared.append(f"{info.functions[at]['prototype']};")  # (not a library's: the C runtime's are its own)
    variables = []
    for d in found.globals:
        at = address(d.section, d.offset)
        if at is not None and names.get(at) == d.name:
            variables.append((d.name, types.get(d.type), True))
    info.header = header.text(variables) if header.entities or variables else ""
    if declared:
        info.header += "\n" + "\n".join(declared) + "\n"


# ------------------------------------------------------------------------------------------------------ map, COFF


def from_map(text: str, timestamp: int, image_base: int | None = None) -> DebugInfo | None:
    """Names and object files from the linker's map, or None when it's another build's (its timestamp) or no map.
    `image_base`: where the program is now (a DLL the project moved), when it isn't the address the map's linker
    preferred."""
    stamp = re.search(r"Timestamp is ([0-9a-fA-F]{8})", text)
    if stamp is None or int(stamp.group(1), 16) != timestamp:
        return None
    preferred = re.search(r"Preferred load address is ([0-9a-fA-F]{8})", text)
    moved = image_base - int(preferred.group(1), 16) if image_base is not None and preferred else 0
    info, used = DebugInfo(), set()
    seen = set()
    for m in re.finditer(r"^\s*[0-9a-fA-F]{4}:[0-9a-fA-F]{8}\s+(\S+)\s+([0-9a-fA-F]{8})\s+(f\s+)?(?:i\s+)?(\S+)\s*$",
                         text, re.M):
        symbol, at, function, obj = m.group(1), int(m.group(2), 16), bool(m.group(3)), m.group(4)
        name = plain_name(symbol)
        at += moved if at else 0
        if not at or at in seen or name is None:
            continue
        seen.add(at)
        info.named.append(symtab.Named(_key(name, used, at), at, 0, function, ".text" if function else ".data"))
        if function and obj not in ("<common>", "<absolute>", "<linker-defined>"):
            info.units[at] = obj.split(":")[-1]
    info.sources = sorted(set(info.units.values()))
    info.named.sort(key=lambda n: n.address)
    return info if info.named else None


def from_coff(data: bytes) -> DebugInfo | None:
    """Names, and the source file of each run of them, from the program's own COFF symbol table; None without one."""
    from .scratch import _pe_header, read_pe, rich_builds

    at = _pe_header(data)
    table, count = struct.unpack_from("<II", data, at + 12)
    if not table or not count or table + COFF_SYMBOL_BYTES * count > len(data):
        return None
    pe = read_pe(data)
    strings = table + COFF_SYMBOL_BYTES * count
    info, used, seen = DebugInfo(), set(), set()
    current, k = "", 0
    # Microsoft's linker writes a symbol's value as its address in the image (its RVA: the demo linked with
    # /DEBUGTYPE:COFF), GNU's as its offset in its section; a Rich header says which linker it was
    microsoft = bool(rich_builds(data))
    while k < count:
        raw, value, section, kind, storage, aux = struct.unpack_from("<8sIhHBB", data, table + COFF_SYMBOL_BYTES * k)
        if raw[:4] == b"\0\0\0\0":
            offset = strings + struct.unpack_from("<I", raw, 4)[0]
            symbol = data[offset:data.find(b"\0", offset)].decode("latin-1")
        else:
            symbol = raw.rstrip(b"\0").decode("latin-1")
        if storage == C_FILE:
            current = data[table + COFF_SYMBOL_BYTES * (k + 1):table + COFF_SYMBOL_BYTES * (k + 1 + aux)] \
                .split(b"\0")[0].decode("latin-1").replace("\\", "/")
        elif storage in (C_EXTERNAL, C_STATIC) and 0 < section <= len(pe.sections):
            address = (pe.image_base if microsoft else pe.sections[section - 1].va) + value
            function = kind & 0xF0 == FUNCTION_TYPE
            name = plain_name(symbol)
            if name and address not in seen:
                seen.add(address)
                info.named.append(symtab.Named(_key(name, used, address), address, 0, function,
                                               pe.sections[section - 1].name))
                if function and current:
                    info.units[address] = current
        k += 1 + aux
    info.sources = sorted(set(info.units.values()))
    info.named.sort(key=lambda n: n.address)
    return info if info.named else None


# ------------------------------------------------------------------------------------------------------ finding it


def find(game: Path, data: bytes, reserved: set[str] = frozenset(), log=print) -> Found | None:
    """The program's own debug information: a matching .pdb beside it (by its own name or the one its CodeView record
    names), else a .map of the same build, else its COFF symbols; None when it has none of these."""
    from .scratch import read_pe

    pe = read_pe(data)
    record = codeview(data)
    names = [game.with_suffix(".pdb").name]
    if record is not None:
        names.append(PureWindowsPath(record[4]).name)
    for name in dict.fromkeys(names):
        path = _beside(game, name)
        if path is None:
            continue
        try:
            found = pdbfile.read(path.read_bytes())
        except (OSError, pdbfile.PdbError) as e:
            log(f"{path.name} isn't a program database decompdecomp can read ({e}): left out")
            continue
        if record is None or not _matches(found, record):
            log(f"{path.name} is the program database of another build of the game (its signature isn't the one "
                "the program names): left out, as its addresses aren't this program's")
            continue
        return Found(from_pdb(found, pe.image_base, reserved), f"{path.name} ({found.kind})")
    path = _beside(game, game.with_suffix(".map").name)
    if path is not None:
        info = from_map(path.read_text(encoding="latin-1", errors="replace"), pe.timestamp, pe.image_base)
        if info is not None:
            return Found(info, path.name)
        log(f"{path.name} is the linker's map of another build of the game (its timestamp isn't the program's): "
            "left out")
    info = from_coff(data)
    return Found(info, "the program's COFF symbols") if info is not None else None


def _beside(game: Path, name: str) -> Path | None:
    """A file in the game's folder, whatever the case of its name (Windows' isn't kept: GAME.PDB, game.pdb)."""
    try:
        return next((p for p in game.parent.iterdir() if p.name.lower() == name.lower() and p.is_file()), None)
    except OSError:
        return None


def _matches(found: pdbfile.Pdb, record: tuple) -> bool:
    kind, guid, signature, age, _ = record
    same = found.guid == guid if kind == "RSDS" else found.signature == signature
    return same and age in (found.age, found.dbi_age)
