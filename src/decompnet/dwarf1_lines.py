"""Shared DWARF 1 attribute and line-table decoding for period compiler objects."""
import struct

FORM_ADDR, FORM_REF, FORM_BLOCK2, FORM_BLOCK4, FORM_DATA2, FORM_DATA4, FORM_DATA8, FORM_STRING = range(1, 9)
FORM_MASK = 0xF
LINE_HEADER_BYTES, LINE_ENTRY_BYTES = 8, 10


def _attributes(data: bytes, at: int, end: int, order: str) -> dict[int, object]:
    """An entry's attributes; one of a form this doesn't know ends them (the entry's length still says where the
    next entry is)."""
    found: dict[int, object] = {}
    fixed = {FORM_ADDR: "I", FORM_REF: "I", FORM_DATA2: "H", FORM_DATA4: "I", FORM_DATA8: "Q"}
    while at + 2 <= end:
        name, = struct.unpack_from(order + "H", data, at)
        at += 2
        form = name & FORM_MASK
        if form in fixed:
            size = struct.calcsize(fixed[form])
            if at + size > end:
                break
            value, = struct.unpack_from(order + fixed[form], data, at)
            at += size
        elif form in (FORM_BLOCK2, FORM_BLOCK4):
            span = 2 if form == FORM_BLOCK2 else 4
            if at + span > end:
                break
            size, = struct.unpack_from(order + ("H" if span == 2 else "I"), data, at)
            value, at = data[at + span:at + span + size], at + span + size
            if at > end:
                break
        elif form == FORM_STRING:
            stop = data.find(b"\0", at, end)
            if stop < 0:
                break
            value, at = data[at:stop].decode("latin-1"), stop + 1
        else:
            break
        found.setdefault(name, value)
    return found


def line_table(data: bytes, offset: int, little: bool = True, include_end: bool = False) -> list[tuple[int, int]]:
    """One compile unit's (address, line) pairs from .line, from its AT_stmt_list offset."""
    order = "<" if little else ">"
    if offset + LINE_HEADER_BYTES > len(data):
        return []
    length, base = struct.unpack_from(order + "II", data, offset)
    end = min(offset + length, len(data))
    out = []
    for at in range(offset + LINE_HEADER_BYTES, end - LINE_ENTRY_BYTES + 1, LINE_ENTRY_BYTES):
        line, _column, delta = struct.unpack_from(order + "IHI", data, at)
        if line == 0:
            if include_end:
                out.append((base + delta, 0))
            break
        out.append((base + delta, line))
    return out

