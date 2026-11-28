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

STREAM_BOOT_DIGEST = '84773819b4533db86725be12c90c63a65d4214deb2819bd6512d8d8ef28ef534'
STREAM_DECODER_DIGEST = '6719121402404c6077d7c3bc2e3b05b263b8c4b34978e0fac6bfa592858745f5'
STREAM_TEXT_DIGEST = 'e7947816066e52aabf23713c1abdd33f7ce40b3e8abcea13156cbaecfb81e020'
STREAM_TEXT_END = 0x51568


def streamed(rom, boot_ranges):
    """Recover an audited loader's least-first stream and its RAM-verified text."""
    if not any(start <= 0x13e0 and end >= 0x2e3c and base + 0x13e0 - start == 0x8004b7e0
               for start, end, base in boot_ranges):
        return None
    if (hashlib.sha256(rom[0x13e0:0x14b4]).hexdigest() != STREAM_BOOT_DIGEST
            or hashlib.sha256(rom[0x2c78:0x2e3c]).hexdigest() != STREAM_DECODER_DIGEST):
        return None
    source = 0x7b31f0
    if len(rom) <= source + 4 or int.from_bytes(rom[source:source + 4], 'big') != 0x6d2d0:
        return None
    try:
        payload = lzhuf.unpack(rom[source + 4:], 0x6d2d0, lsb_variant=True)
    except (AssetError, IndexError, ValueError):
        return None
    if hashlib.sha256(payload[:STREAM_TEXT_END]).hexdigest() != STREAM_TEXT_DIGEST:
        return None
    return ((0x800cf4e0, payload[:STREAM_TEXT_END], payload[STREAM_TEXT_END:]),)


def find(rom, boot_ranges):
    """Require the audited loader, its source descriptor and its called output."""
    if program := streamed(rom, boot_ranges):
        return program
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
