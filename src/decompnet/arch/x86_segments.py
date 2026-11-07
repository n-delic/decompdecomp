"""Proofs for DOS LE's two-byte selector/offset fixups, which objdiff's COFF reader cannot evaluate.

The OMF converter checks exact source sites and symbolic targets before replacing loader fields with the original
words. Absolute COFF symbols carry that proof through renaming, the judge, progress reports and exported rebuilds.
A literal with the same bytes cannot substitute for a loader fixup. Standard library only; exported as segments.py.
"""

import re
import struct

PREFIX = "__decomp_segment_"


def marker(function, site):
    offset, kind, base, target, _raw = site
    return PREFIX + function.encode().hex() + f"_{offset:X}_{kind}_{base:08X}_{target:08X}"


def grouped(names):
    out = {}
    for name in names:
        if name.startswith(PREFIX):
            try:
                function = bytes.fromhex(name[len(PREFIX):].split("_", 1)[0]).decode()
            except (ValueError, UnicodeError):
                raise ValueError("Invalid segmented-fixup proof symbol.") from None
            out.setdefault(function, set()).add(name)
    return out


def proofs(data):
    """Read proof symbols from COFF; ELF and objects with no proof symbols have no segmented verdict."""
    if len(data) < 20 or data[:2] != b"\x4c\x01":
        return {}
    table, count = struct.unpack_from("<II", data, 8)
    strings = table + count * 18
    if strings + 4 > len(data):
        raise ValueError("Truncated COFF symbol table.")
    names, index = [], 0
    while index < count:
        at = table + index * 18
        raw, value, section, typ, storage, aux = struct.unpack_from("<8sIhHBB", data, at)
        if raw[:4] == bytes(4):
            offset = strings + struct.unpack_from("<I", raw, 4)[0]
            if offset < strings + 4 or offset >= len(data):
                raise ValueError("Invalid COFF proof name offset.")
            raw = data[offset:data.index(b"\0", offset)]
        name = raw.rstrip(b"\0").decode("latin1")
        if name.startswith(PREFIX):
            if (value, section, typ, storage, aux) != (0, -1, 0, 3, 0):
                raise ValueError("Invalid COFF segmented-fixup proof.")
            names.append(name)
        index += 1 + aux
    return grouped(names)


def conflicts(target, base):
    """Per-function failures, including stale objects without the required proof and invented extra fixups."""
    try:
        expected = file_proofs(target)
        actual = file_proofs(base)
    except (OSError, ValueError, struct.error):
        # A valid target with segmented sites must never accept an unreadable candidate.
        try:
            expected = file_proofs(target)
        except (OSError, ValueError, struct.error):
            return {}
        actual = {}
    return {fn: "DOS selector/16-bit fixups differ or lack a verified symbolic target"
            for fn in expected.keys() | actual.keys() if expected.get(fn, set()) != actual.get(fn, set())}


def file_proofs(path):
    """Reports touch every console: skip ELF after its header and read only COFF's symbol/string tables."""
    with path.open("rb") as stream:
        head = bytearray(stream.read(20))
        if len(head) < 20 or head[:2] != b"\x4c\x01":
            return {}
        table, count = struct.unpack_from("<II", head, 8)
        if table < 20 or count > (64 << 20) // 18:
            raise ValueError("Invalid COFF proof table.")
        stream.seek(table)
        data = stream.read((64 << 20) + 1)
        if len(data) > 64 << 20 or len(data) < count * 18 + 4:
            raise ValueError("Truncated or oversized COFF proof table.")
        if PREFIX.encode() not in data:
            return {}
        struct.pack_into("<I", head, 8, 20)
        return proofs(bytes(head) + data)


class Resolver:
    """Resolve only named external addresses against this LE's object table; no inferred DGROUP selectors."""

    def __init__(self, info, image, names):
        self.names, self.real = names, names.real_names(info)
        self.functions = {f"func_{f['addr']:08X}".encode(): f for fs in info["units"].values() for f in fs}
        self.objects = [(base, base + len(buf)) for base, _, buf in image.objects]
        self.seen = set()

    def function(self, name):
        return self.functions.get(self.names.address_of(name.encode("latin1"), self.real))

    def resolve(self, owner, offset, location, external, addend):
        function = self.function(owner)
        key = self.names.address_of(external.encode("latin1"), self.real)
        match = re.fullmatch(rb"(?:D|func)_([0-9A-Fa-f]{8})", key or b"")
        if function is None or match is None:
            raise ValueError("A DOS segmented fixup needs a func_XXXXXXXX or D_XXXXXXXX external address.")
        address = int(match[1], 16)
        site = next((s for s in function.get("segments", []) if s[0] == offset), None)
        kind = 2 if location == 2 else 5
        if site is None or site[1] != kind:
            raise ValueError("The compiler's selector/16-bit fixup is at a different instruction or has another kind.")
        if kind == 2:
            correct = not addend and any(lo == site[2] and lo <= address < hi for lo, hi in self.objects)
        else:
            correct = address + addend == site[2] + site[3]
        if not correct:
            raise ValueError("The compiler's selector/16-bit fixup refers to a different LE object or offset.")
        self.seen.add((function["name"], offset))
        return site[4]

    def finish(self, public_names):
        result = []
        for name in public_names:
            function = self.function(name)
            if function is None:
                continue
            for site in function.get("segments", []):
                if (function["name"], site[0]) not in self.seen:
                    raise ValueError("A DOS selector/16-bit field was compiled as a literal or lost its symbolic fixup.")
                result.append((marker(function["name"], site), 0, -1, 0, 3))
        return result
