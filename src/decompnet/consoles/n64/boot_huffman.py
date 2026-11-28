"""Recover a boot program packed with adaptive Huffman and a sliding window."""

import hashlib
import struct

from decompassets.formats import AssetError
from decompassets.n64 import lzhuf

# Full loader and expanded text digests corroborate the exact layout: the text
# was also compared with the running game's RAM before this boundary was added.
# Data after the text changes during execution and must stay data in the split.
LOADER_DIGEST = 'aeec3ea426fc49f221c3e56992d24a24ec472f12a31cccba4c02a7b4c3bcc1c3'
TEXT_DIGEST = '80bac6f4fe08ab2a07d0c9f74075dbc6d6ce06a8c1cd842c4e32f91751a6cff9'
TEXT_END = 0xcdb2c


def find(rom, boot_ranges):
    """Require the audited loader, its source descriptor and its called output."""
    if not any(start <= 0x1050 and end >= 0x1f30 and base + 0x1050 - start == 0x80000450
               for start, end, base in boot_ranges):
        return None
    if hashlib.sha256(rom[0x1050:0x1f30]).hexdigest() != LOADER_DIGEST:
        return None
    if len(rom) < 0x1f40 or rom[0x1f30:0x1f38] != b'Ogre\x10\x00\x00\x01':
        return None
    source, end = struct.unpack_from('>II', rom, 0x1f38)
    if source != 0x2ac0 or not source + 4 < end <= len(rom):
        return None
    size = int.from_bytes(rom[source:source + 4], 'big')
    if size != 0xec880:
        return None
    try:
        payload = lzhuf.unpack(rom[source + 4:end], size, fill=0)
    except (AssetError, IndexError, ValueError):
        return None
    if hashlib.sha256(payload[:TEXT_END]).hexdigest() != TEXT_DIGEST:
        return None
    return ((0x80001ec0, payload[:TEXT_END], payload[TEXT_END:]),)
