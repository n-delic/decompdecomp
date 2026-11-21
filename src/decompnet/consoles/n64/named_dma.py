"""Verified named cartridge directory with a shared code slot."""

import hashlib
import struct

from .raw_slots import Slot

_ROW = struct.Struct('>4I')
# The resident directory's first entry covers the boot program. Seven
# alternatives are copied to one slot. The loader places the next bank at
# the adjacent subrange used by its direct calls and cache maintenance.
_DIRECTORY = (0x48030, 148, '20252407bf3aa1bd14ea6ec56c3f0914cdfd497aae1c7a7ee4f961fa588c71ef')
_LOADER = (0xe1cc, 484, '712bad98dd9332e51fb8133e69d0ee286617a71789e5b02ddde4a2e04f9b42d3')


def _matches(image, signature):
    start, size, digest = signature
    return hashlib.sha256(image[start:start+size]).hexdigest() == digest


def read(image, resident):
    """Keep the measured layout scoped to the verified loader and directory."""
    if not all(_matches(image, signature) for signature in (_DIRECTORY, _LOADER)):
        return []
    offset, size, _ = _DIRECTORY
    if not any(start <= _LOADER[0] < _LOADER[0]+_LOADER[1] <= end for start,end,_ in resident):
        return []
    slots = []
    for index, (_, _, start, end) in enumerate(_ROW.iter_unpack(image[offset+4:offset+size])):
        if not index:
            continue
        if end > len(image):
            return []
        slots.append(Slot(start, end, 0x80170000 if index < 8 else 0x80178df0))
    return slots
