"""The flat 386 OMF records used by Watcom 10.5's homemade C++ probe (#81), translated to COFF for objdiff.

The PC build uses this before objdiff. Narrow external fixups need a verified DOS LE context; unsupported records
fail closed, since losing a fixup would weaken the judge. Record and fixup numbers: Open Watcom's pcobj.h; the
checked subset has SEGDEF32, LEDATA32, PUBDEF32 and explicit 32-bit FIXUPP32 targets (no threads or COMDAT yet).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


class OMFError(ValueError):
    """An OMF feature the flat COFF conversion cannot preserve, or a malformed object."""


class Reader:
    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.data):
            raise OMFError("Truncated OMF record. Compile the source again.")
        result = self.data[self.offset:self.offset + count]
        self.offset += count
        return result

    def number(self, count: int = 1) -> int:
        return int.from_bytes(self.take(count), "little")

    def index(self) -> int:
        first = self.number()
        return ((first & 0x7F) << 8) | self.number() if first & 0x80 else first

    def name(self) -> str:
        return self.take(self.number()).decode("latin1")

    def more(self) -> bool:
        return self.offset < len(self.data)


@dataclass
class Segment:
    name: str
    kind: str
    size: int
    alignment: int
    data: bytearray
    written: list[tuple[int, int]] = field(default_factory=list)
    relocations: list[tuple[int, int, int]] = field(default_factory=list)


MAX_SEGMENT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
DIR32, REL32 = 0x06, 0x14
# Metadata which doesn't affect the linked bytes. All other records need an implementation before acceptance.
METADATA = {0x94, 0x95}
COMMENTS = {0x00, 0x9B, 0x9F, 0xAA, 0xE9, 0xFE}  # translator/model, default libraries, ABI, source and debug options


def _item(items: list, index: int):
    if not 0 < index < len(items):
        raise OMFError("Invalid OMF index. Compile the source again.")
    return items[index]


def convert(data: bytes, segmented=None) -> bytes:
    """Preserve public symbols and DIR32/REL32 fixups; verify narrow fixups against optional DOS context.

    shortcut: explicit fixups only, with FLAT or target frames. Add thread/COMDAT/LIDATA support from real
    compiler probes before enabling those records; never pretend their data or relocations are optional.
    """
    if len(data) > MAX_TOTAL_BYTES:
        raise OMFError("OMF input exceeds the 64 MiB conversion limit.")
    names, segments, groups, externals = [""], [None], [None], [None]
    symbols = []  # (name, value, section, type, storage); segment symbols precede all fixup targets
    fixups = []  # segment, offset, target kind, target index, displacement, self relative
    source = Reader(data)
    started, ended, last_data, total_bytes = False, False, None, 0
    while source.more():
        kind, size = source.number(), source.number(2)
        if not size:
            raise OMFError("Invalid OMF record length. Compile the source again.")
        raw = source.take(size)
        if raw[-1] and (kind + (size & 255) + (size >> 8) + sum(raw)) % 256:
            raise OMFError("Bad OMF checksum. Compile the source again.")
        if ended:
            raise OMFError("Data follows OMF MODEND. Convert one object at a time.")
        r = Reader(raw[:-1])
        if not started:
            if kind not in (0x80, 0x82):
                raise OMFError("Expected an OMF module header. Convert one .obj, not a library.")
            r.name()
            started = True
        elif kind in (0x8A, 0x8B):
            if r.number() != 0:
                raise OMFError("An OMF entry point is not supported. Convert an unlinked compiler object.")
            ended = True
        elif kind in METADATA:
            continue
        elif kind == 0x88:
            r.number()  # comment attributes
            comment = r.number()
            if comment not in COMMENTS:
                raise OMFError("This OMF comment class is not supported; it may change symbol resolution.")
            if comment == 0xFE and r.number() not in (ord("D"), ord("O")):
                raise OMFError("This OMF linker directive is not supported; it may change symbol resolution.")
            continue
        elif kind == 0x96:
            while r.more():
                names.append(r.name())
        elif kind == 0x99:
            if len(segments) > 32767:
                raise OMFError("Too many OMF segments for standard COFF.")
            attrs, length = r.number(), r.number(4)
            align = attrs >> 5
            if not attrs & 1 or attrs & 2 or align not in (1, 2, 3, 4, 5):
                raise OMFError("Only relocatable flat USE32 OMF segments are supported.")
            if (attrs >> 2) & 7 not in (0, 2):
                raise OMFError("This OMF segment combine mode is not supported.")
            name, cls = _item(names, r.index()), _item(names, r.index())
            _item(names, r.index())  # overlay name
            total_bytes += length
            if length > MAX_SEGMENT_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise OMFError("OMF segments exceed the 64 MiB conversion limit.")
            segments.append(Segment(name, cls, length, {1: 1, 2: 2, 3: 16, 4: 256, 5: 4}[align], bytearray(length)))
        elif kind == 0x9A:
            name, members = _item(names, r.index()), []
            while r.more():
                if r.number() != 0xFF:
                    raise OMFError("Unsupported OMF group member.")
                member = r.index()
                _item(segments, member)
                members.append(member)
            groups.append((name, members))
        elif kind == 0x8C:
            while r.more():
                externals.append(r.name())
                r.index()  # type index has no effect on relocation arithmetic
        elif kind == 0x91:
            group, seg = r.index(), r.index()
            if group:
                _item(groups, group)
            segment = _item(segments, seg)  # absolute symbols (segment zero) need another representation
            while r.more():
                name, value = r.name(), r.number(4)
                r.index()
                if value > segment.size:
                    raise OMFError("OMF public symbol is outside its segment.")
                symbols.append((name, value, seg, 0x20 if segment.kind == "CODE" else 0, 2))
        elif kind == 0xA1:
            seg, offset = r.index(), r.number(4)
            segment = _item(segments, seg)
            block = r.take(len(r.data) - r.offset)
            end = offset + len(block)
            if end > segment.size or any(offset < b and a < end for a, b in segment.written):
                raise OMFError("OMF data overlaps or exceeds its segment.")
            segment.data[offset:end] = block
            segment.written.append((offset, end))
            last_data = seg, offset, len(block)
        elif kind == 0x9D:
            if last_data is None:
                raise OMFError("OMF fixup has no preceding data record.")
            while r.more():
                loc = r.number()
                if not loc & 0x80:
                    raise OMFError("OMF fixup threads are not supported yet.")
                offset = ((loc & 3) << 8) | r.number()
                location = (loc >> 2) & 15
                if location not in (1, 2, 9) or (location != 9 and (segmented is None or not loc & 0x40)):
                    raise OMFError("This OMF fixup needs supported 32-bit offsets or a DOS LE segmented context.")
                fix = r.number()
                if fix & 0x88:
                    raise OMFError("OMF fixup threads are not supported yet.")
                frame = (fix >> 4) & 7
                if frame == 1:
                    group = _item(groups, r.index())
                    if group != ("FLAT", []):
                        raise OMFError("Only FLAT group frames are supported.")
                elif frame != 5:
                    raise OMFError("Only FLAT or target OMF frames are supported.")
                target_kind, target = fix & 3, r.index()
                if target_kind not in (0, 2):
                    raise OMFError("OMF group and absolute fixup targets are not supported.")
                displacement = 0 if fix & 4 else r.number(4)
                seg, base, length = last_data
                if offset + (4 if location == 9 else 2) > length:
                    raise OMFError("OMF fixup is outside its data record.")
                fixups.append((seg, base + offset, target_kind, target, displacement, not loc & 0x40, location))
        else:
            raise OMFError(f"OMF record 0x{kind:02X} is not supported. Keep the original object for local testing.")
        if r.more():
            raise OMFError(f"Unexpected bytes in OMF record 0x{kind:02X}.")
    if not ended:
        raise OMFError("OMF MODEND is missing. Compile the source again.")
    # Static segment symbols make internal offsets and switch tables relocatable without inventing functions.
    segment_symbols = [(s.name, 0, i, 0, 3) for i, s in enumerate(segments[1:], 1)]
    public_count = len(symbols)
    symbols = [*segment_symbols, *symbols, *((n, 0, 0, 0, 2) for n in externals[1:])]
    seen = set()
    for seg, offset, kind, target, displacement, relative, location in fixups:
        _item(segments if kind == 0 else externals, target)
        width = 4 if location == 9 else 2
        if any((seg, byte) in seen for byte in range(offset, offset + width)):
            raise OMFError("Overlapping OMF fixups are not supported.")
        seen.update((seg, byte) for byte in range(offset, offset + width))
        segment = segments[seg]
        if location != 9:
            if kind != 2 or segment.kind != "CODE":
                raise OMFError("Segmented OMF fixups currently need an external address in a code function.")
            owners = [(value, name) for name, value, section, typ, _ in symbols if section == seg and typ & 0x20 and value <= offset]
            if not owners:
                raise OMFError("A segmented OMF fixup has no public function owner.")
            start, owner = max(owners)
            inline = struct.unpack_from("<H", segment.data, offset)[0]
            try:
                word = segmented.resolve(owner, offset - start, location, externals[target], inline + displacement)
            except ValueError as exc:
                raise OMFError(str(exc)) from exc
            struct.pack_into("<H", segment.data, offset, word)
            continue
        inline = struct.unpack_from("<I", segment.data, offset)[0]
        struct.pack_into("<I", segment.data, offset, (inline + displacement) & 0xFFFFFFFF)
        symbol = target - 1 if kind == 0 else len(segment_symbols) + public_count + target - 1
        segment.relocations.append((offset, symbol, REL32 if relative else DIR32))
    if segmented is not None:
        try:
            symbols.extend(segmented.finish([name for name, _, _, typ, _ in symbols if typ & 0x20]))
        except ValueError as exc:
            raise OMFError(str(exc)) from exc
    return _coff(segments[1:], symbols)


def _coff(segments: list[Segment], symbols: list[tuple]) -> bytes:
    """Write COFF with the original section offsets, including uninitialised storage and long symbol names."""
    strings = bytearray(4)

    def string_offset(text):
        offset = len(strings)
        strings.extend(text.encode("latin1") + b"\0")
        return offset

    sections, payload = bytearray(), bytearray()
    cursor = 20 + 40 * len(segments)
    for segment in segments:
        if len(segment.relocations) > 65535:
            raise OMFError("Too many relocations for a COFF section.")
        # The PC delinker calls code sections .text. Objdiff pairs sections before functions, so keeping Watcom's
        # _TEXT here would leave an otherwise identical named function unpaired. Segment symbols keep their names.
        section_name = ".text" if segment.kind == "CODE" else segment.name
        name = section_name.encode("latin1")
        name = name if len(name) <= 8 else f"/{string_offset(section_name)}".encode()
        bss = segment.kind == "BSS" and not segment.written
        flags = 0x60000020 if segment.kind == "CODE" else 0xC0000080 if bss else 0xC0000040
        flags |= (segment.alignment.bit_length()) << 20
        raw = b"" if bss else bytes(segment.data)
        relocs = b"".join(struct.pack("<IIH", *r) for r in segment.relocations)
        sections.extend(struct.pack("<8sIIIIIIHHI", name.ljust(8, b"\0"), 0, 0, segment.size,
                                    cursor if raw else 0, cursor + len(raw) if relocs else 0, 0,
                                    len(segment.relocations), 0, flags))
        payload.extend(raw + relocs)
        cursor += len(raw) + len(relocs)
    table = bytearray()
    for name, value, section, typ, storage in symbols:
        raw = name.encode("latin1")
        field = raw.ljust(8, b"\0") if len(raw) <= 8 else struct.pack("<II", 0, string_offset(name))
        table.extend(struct.pack("<8sIhHBB", field, value, section, typ, storage, 0))
    struct.pack_into("<I", strings, 0, len(strings))
    return bytes(struct.pack("<HHIIIHH", 0x14C, len(segments), 0, cursor, len(symbols), 0, 0)
                 + sections + payload + table + strings)
