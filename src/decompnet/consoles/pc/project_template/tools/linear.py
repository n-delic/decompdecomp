"""Read flat 32-bit LE DOS-extender programs through the PC pipeline's PE memory view.

Only the view is relocated; the user's executable is never changed. Rebuild reverses the page mapping and keeps
its DOS stub, fixup tables, uninitialised bytes and overlays exactly. Layout and fixup flags follow Open Watcom's
bld/watcom/h/exeflat.h and bld/exedump/c/wdfix.c. Selectors and 16-bit offsets retain symbolic target metadata;
the OMF converter checks those targets before preserving the original loader fields for byte comparison.
Standard library only: this file travels with exported projects.
"""

import struct

MAX_IMAGE = 64 << 20
MAX_OBJECTS = 256
HEADER_SIZE = 196


def header(data: bytes) -> int | None:
    """Locate an LE header through MZ's pointer, including stubs larger than the sniff buffer."""
    if len(data) < 64 or data[:2] != b"MZ":
        return None
    at = struct.unpack_from("<I", data, 60)[0]
    return at if at >= 64 and data[at:at + 2] == b"LE" else None


class Linear:
    """A validated LE image, its page mapping and loader fixups; rejects formats we cannot rebuild faithfully."""

    def __init__(self, data: bytes):
        self.original = data
        self.at = header(data)
        if self.at is None:
            raise ValueError("Choose a 32-bit LE DOS-extender executable.")
        self._slice(self.at, HEADER_SIZE)
        if data[self.at + 2:self.at + 8] != bytes(6) or self._u16(self.at + 8) not in (2, 3):
            raise ValueError("Only little-endian 386/486 LE programs are supported.")
        if self.word(16) & 0x38000 or self.word(116):
            raise ValueError("LE DLLs, device drivers and imported OS/2 modules are not supported.")
        self.page_size, last, total = self.word(40), self.word(44), self.word(20)
        count = self.word(68)
        if not 1 <= count <= MAX_OBJECTS or self.page_size != 4096 or not 1 <= total <= MAX_IMAGE // self.page_size:
            raise ValueError("The LE object/page counts or page size are unsupported.")
        if last > self.page_size:
            raise ValueError("The LE last-page size is invalid.")
        self._slice(self.at + self.word(64), count * 24)
        self._slice(self.at + self.word(72), total * 4)
        physical_count = max((int.from_bytes(self._slice(self.at + self.word(72) + i * 4, 3), "big")
                              for i in range(total) if self.original[self.at + self.word(72) + i * 4 + 3] == 0), default=0)
        self.objects, self.pages, self.fixups, self.segment_fixups = [], [], {}, []
        self.segment_targets = {}  # linear source -> (LE kind, target object base, full target offset, raw word)
        used, physical = set(), set()
        for index in range(count):
            size, base, flags, first, pages, _ = struct.unpack_from("<6I", data, self.at + self.word(64) + index * 24)
            if not size or size > MAX_IMAGE or base < 4096 or base + size > 0xFFFFFFFF or not flags & 0x2000:
                raise ValueError("Only flat USE32 LE objects with nonzero linear addresses are supported.")
            if any(base < b + len(buf) and b < base + size for b, _, buf in self.objects):
                raise ValueError("The LE objects overlap in memory.")
            if sum(len(buf) for _, _, buf in self.objects) + size > MAX_IMAGE:
                raise ValueError("The LE image exceeds the 64 MiB analysis limit.")
            if pages and (first < 1 or first + pages - 1 > total or (pages - 1) * self.page_size >= size):
                raise ValueError("The LE object's page range is invalid.")
            buf = bytearray(size)
            for local in range(pages):
                number = first - 1 + local
                if number in used:
                    raise ValueError("The LE objects share a page-map entry.")
                used.add(number)
                raw = self._slice(self.at + self.word(72) + number * 4, 4)
                page, kind = int.from_bytes(raw[:3], "big"), raw[3]
                length = min(self.page_size, size - local * self.page_size)
                offset, stored = None, 0
                if kind == 0:
                    if not 1 <= page <= total or page in physical:
                        raise ValueError("The LE physical page mapping is invalid or aliased.")
                    physical.add(page)
                    stored = min(length, (last or self.page_size) if page == physical_count else self.page_size)
                    offset = self.word(128) + (page - 1) * self.page_size
                    buf[local * self.page_size:local * self.page_size + stored] = self._slice(offset, stored)
                elif kind != 3:
                    raise ValueError("Iterated, invalid and compressed LE pages are not supported.")
                self.pages.append((number, index, local * self.page_size, offset, stored, length))
            self.objects.append((base, flags, buf))
        entry_obj, entry_off = self.word(24), self.word(28)
        if not 1 <= entry_obj <= count or entry_off >= len(self.objects[entry_obj - 1][2]) or not self.objects[entry_obj - 1][1] & 4:
            raise ValueError("The LE entry point is outside executable memory.")
        self.entry = self.objects[entry_obj - 1][0] + entry_off
        self.raw_objects = [bytes(buf) for _, _, buf in self.objects]
        self._fixups(total)

    def _slice(self, at: int, length: int) -> bytes:
        if at < 0 or length < 0 or at + length > len(self.original):
            raise ValueError("The LE executable has a truncated or invalid table/page.")
        return self.original[at:at + length]

    def _u16(self, at: int) -> int:
        return int.from_bytes(self._slice(at, 2), "little")

    def word(self, relative: int) -> int:
        return int.from_bytes(self._slice(self.at + relative, 4), "little")

    def _fixups(self, total: int) -> None:
        table, records = self.at + self.word(104), self.at + self.word(108)
        self._slice(table, (total + 1) * 4)
        offsets = struct.unpack_from(f"<{total + 1}I", self.original, table)
        if offsets[0] or any(b < a for a, b in zip(offsets, offsets[1:])):
            raise ValueError("The LE fixup page table is invalid.")
        if records + offsets[-1] > self.at + self.word(112):
            raise ValueError("The LE fixup records overlap another table.")
        self._slice(records, offsets[-1])
        mapped = {number: (obj, local) for number, obj, local, _, _, _ in self.pages}
        for page in range(total):
            pos, end = records + offsets[page], records + offsets[page + 1]
            if page not in mapped and pos != end:
                raise ValueError("An LE fixup names an unmapped page.")
            while pos < end:
                def take(size, signed=False, limit=end):
                    nonlocal pos
                    if pos + size > limit:
                        raise ValueError("An LE fixup record is truncated.")
                    value = int.from_bytes(self.original[pos:pos + size], "little", signed=signed)
                    pos += size
                    return value
                source, flags = take(1), take(1)
                kind = source & 15
                if source & ~0x2F or kind not in (2, 5, 7, 8) or flags & ~0x50:
                    raise ValueError("This LE uses unsupported fixups (imports, chains, far pointers or additive records).")
                count = take(1) if source & 32 else 0
                sources = [] if source & 32 else [take(2, signed=True)]
                target_obj = take(2 if flags & 64 else 1)
                target_off = 0 if kind == 2 else take(4 if flags & 16 else 2)
                if not 1 <= target_obj <= len(self.objects) or target_off >= len(self.objects[target_obj - 1][2]):
                    raise ValueError("An LE fixup points outside its target object.")
                sources += [take(2, signed=True) for _ in range(count)]
                obj, local = mapped[page]
                base, _, buf = self.objects[obj]
                target = self.objects[target_obj - 1][0] + target_off
                for off in sources:
                    at, width = local + off, 2 if kind in (2, 5) else 4
                    if at < 0 or at + width > len(buf):
                        raise ValueError("An LE fixup points outside its source object.")
                    key = obj, at
                    if kind in (2, 5):
                        self.segment_fixups.append((base + at, width))
                        target_info = (kind, self.objects[target_obj - 1][0], target_off,
                                       int.from_bytes(buf[at:at + 2], "little"))
                        if base + at in self.segment_targets and self.segment_targets[base + at] != target_info:
                            raise ValueError("Conflicting LE segmented fixups name the same bytes.")
                        self.segment_targets[base + at] = target_info
                        continue  # selector/16-bit addressing is retained, never invented as a flat pointer
                    value = (target - (base + at + 4) if kind == 8 else target) & 0xFFFFFFFF
                    if key in self.fixups and self.fixups[key] != value:
                        raise ValueError("Conflicting LE fixups name the same bytes.")
                    self.fixups[key] = value
                    struct.pack_into("<I", buf, at, value)
        self.segment_fixups = sorted(set(self.segment_fixups))
        spans = sorted([(self.objects[obj][0] + off, 4) for obj, off in self.fixups] + self.segment_fixups)
        if any(at < before + width for (before, width), (at, _) in zip(spans, spans[1:])):
            raise ValueError("The LE loader fixups overlap.")

    def view(self) -> bytes:
        """A deterministic PE memory view for the existing x86 analysis, delinker and judge."""
        # PE maps its headers at image_base. Reserve a page before LE's first object so Ghidra doesn't put
        # the first code page into an overlay that entry-point analysis cannot follow.
        base = min(b for b, _, _ in self.objects) - 4096
        headers = (0x178 + len(self.objects) * 40 + 511) & ~511
        out = bytearray(headers)
        out[:2] = b"MZ"
        struct.pack_into("<I", out, 60, 0x80)
        out[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", out, 0x84, 0x14C, len(self.objects), 0, 0, 0, 224, 0x102)
        opt = 0x98
        struct.pack_into("<H", out, opt, 0x10B)
        struct.pack_into("<I", out, opt + 16, self.entry - base)
        struct.pack_into("<I", out, opt + 20, next(addr - base for addr, flags, _ in self.objects if flags & 4))
        struct.pack_into("<III", out, opt + 28, base, 4096, 512)
        struct.pack_into("<II", out, opt + 56, (max(b + len(buf) for b, _, buf in self.objects) - base + 4095) & ~4095, headers)
        struct.pack_into("<H", out, opt + 68, 3)
        struct.pack_into("<I", out, opt + 92, 16)
        for number, (addr, flags, buf) in enumerate(self.objects):
            name = f".text{number}" if flags & 4 else f".data{number}"
            chars = 0x40000000 | (0x20000020 if flags & 4 else 0x40) | (0x80000000 if flags & 2 else 0)
            raw_size = (len(buf) + 511) & ~511
            struct.pack_into("<8sIIIIIIHHI", out, 0x178 + number * 40, name.encode(), len(buf), addr - base,
                             raw_size, len(out), 0, 0, 0, 0, chars)
            out += buf + bytes(raw_size - len(buf))
        return bytes(out)

    def restore(self, view: bytes) -> bytes:
        """Put the view's code back into the original LE; loader fields and unmapped bytes stay original."""
        expected = self.view()
        headers = struct.unpack_from("<I", expected, 0x98 + 60)[0]
        if len(view) != len(expected) or view[:headers] != expected[:headers]:
            raise ValueError("The LE memory view's headers or size changed during rebuild.")
        out = bytearray(self.original)
        starts = [struct.unpack_from("<I", view, 0x178 + i * 40 + 20)[0] for i in range(len(self.objects))]
        memory = [bytearray(view[start:start + len(buf)]) for start, (_, _, buf) in zip(starts, self.objects)]
        # Restore loader placeholders before copying pages, including cross-page and zero-fill fixups.
        for (obj, at), value in self.fixups.items():
            if struct.unpack_from("<I", memory[obj], at)[0] != value:
                raise ValueError("A rebuilt LE loader fixup changed its target.")
            memory[obj][at:at + 4] = self.raw_objects[obj][at:at + 4]
        for _, obj, local, offset, stored, length in self.pages:
            chunk = memory[obj][local:local + length]
            if offset is None:
                if any(chunk):
                    raise ValueError("A rebuilt LE function changes a zero-filled page.")
                continue
            if any(chunk[stored:]):
                raise ValueError("A rebuilt LE function changes unstored bytes.")
            out[offset:offset + stored] = chunk[:stored]
        for obj, buf in enumerate(memory):
            end = max((local + length for _, number, local, _, _, length in self.pages if number == obj), default=0)
            if buf[end:] != self.raw_objects[obj][end:]:
                raise ValueError("A rebuilt LE function changes unmapped object bytes.")
        return bytes(out)
