"""Recover an audited executable from a byte-run and backreference loader."""

import hashlib

from decompassets.n64 import paired_rle_images

SOURCE = 0x4e69a8
PACKED = 0x55dd4
SIZE = 0x89cf0
TEXT_END = 0x4b840
LOADER_DIGEST = '87aef98abdd23c11c500a0b7d0afd209ad2d16e23903744379d9d49e24f2bfa1'
OUTPUT_DIGEST = 'd56be57d6000ad0c0be29e2e280dd598be6ad7a6814e927c9ed290487e353c0e'
OVERLAY_SOURCE = 0x5f1190
OVERLAY_PACKED = 0xad36
OVERLAY_SIZE = 0x10b50
OVERLAY_TEXT_END = 0xd63c
OVERLAY_DIGEST = 'f5606f81f3d0732930c146c74d98af7f75521ae63ac54a453f301a6075619c24'


def present(rom):
    """Require exact audited loader and expanded-image bytes before mapping them."""
    if len(rom) < SOURCE + PACKED or hashlib.sha256(rom[0x4424:0x4b10]).hexdigest() != LOADER_DIGEST:
        return None
    data = paired_rle_images.decode(rom[SOURCE:SOURCE + PACKED], expected=None, max_output=SIZE)
    if data is None or len(data) != SIZE or hashlib.sha256(data).hexdigest() != OUTPUT_DIGEST:
        return None
    primary = (0x80107830, data[:TEXT_END], data[TEXT_END:])
    if len(rom) < OVERLAY_SOURCE + OVERLAY_PACKED:
        return (primary,)
    overlay = paired_rle_images.decode(rom[OVERLAY_SOURCE:OVERLAY_SOURCE + OVERLAY_PACKED],
                                       expected=None, max_output=OVERLAY_SIZE)
    if (overlay is None or len(overlay) != OVERLAY_SIZE
            or hashlib.sha256(overlay).hexdigest() != OVERLAY_DIGEST):
        return (primary,)
    # The game loads the same image at both addresses; both complete copies and
    # their executable ranges were compared with RAM at the same retrace.
    return (primary, *((vram, overlay[:OVERLAY_TEXT_END], overlay[OVERLAY_TEXT_END:])
                       for vram in (0x801bf1a0, 0x801fa948)))


def find(rom, boot_ranges):
    """Place only the loader whose mapping was confirmed in the running game."""
    from .boot_gzip import _coalesce_boot_ranges

    if not any(start <= 0x4424 and end >= 0x4b10 and base + 0x4424 - start == 0x80003824
               for start, end, base in _coalesce_boot_ranges(boot_ranges)):
        return None
    return present(rom)
