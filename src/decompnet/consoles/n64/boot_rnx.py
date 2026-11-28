"""Recover an audited boot program stored in CRC-checked RNX pages."""

import hashlib
import struct

from decompassets import rnc

SOURCE = 0x6fe48c
END = 0x70f7d0
OUTPUT_SIZE = 0xc8648
CODE_START = 0xc4d0
CODE_END = 0x196b4
LOADER_DIGEST = '685b75d22595b2287e87b4c4684952deb8400256441f0efe39d74c0b1addc6d7'
OUTPUT_DIGEST = '4d854292a9a8360287eba408aff2a6c0bd958c07564415f333064e2ba4f2318c'


def _decode(rom):
    """Decode bounded RNC pages and fills through the stream terminator."""
    if END > len(rom):
        return None
    at = SOURCE
    output = bytearray()
    try:
        while at + 4 <= END:
            tag = rom[at:at + 4]
            if tag == b'END!':
                return bytes(output) if at + 4 == END and len(output) == OUTPUT_SIZE else None
            if tag == b'FILL':
                if at + 8 > END:
                    return None
                size, fill, reserved = struct.unpack_from('>HBB', rom, at + 4)
                if not size or reserved:
                    return None
                output.extend(bytes([fill]) * size)
                at += 8
            elif tag in (b'RNX\1', b'RNC\1'):
                if at + 18 > END:
                    return None
                size, packed = struct.unpack_from('>II', rom, at + 4)
                following = at + ((18 + packed + 7) & ~7)
                if not 0 < size <= 0x4000 or not 0 < packed <= 0x4040 or following > END:
                    return None
                if any(rom[at + 18 + packed:following]):
                    return None
                output.extend(rnc.unpack(b'RNC\1' + rom[at + 4:at + 18 + packed]))
                at = following
            else:
                return None
            if len(output) > OUTPUT_SIZE:
                return None
    except (rnc.RncError, ValueError, struct.error):
        return None
    return None


def present(rom):
    """Identify the audited bytes before the loader's own mapping is discovered."""
    if hashlib.sha256(rom[0xa6d8:0xa980]).hexdigest() != LOADER_DIGEST:
        return None
    payload = _decode(rom)
    if payload is None or hashlib.sha256(payload).hexdigest() != OUTPUT_DIGEST:
        return None
    return ((0x800b82b0, payload[:CODE_END], payload[CODE_END:]),)


def find(rom, boot_ranges):
    """Require the audited loader at its verified address and its decoded image."""
    if not any(start <= 0xa6d8 and end >= 0xa980 and base + 0xa6d8 - start == 0x80009ad8
               for start, end, base in boot_ranges):
        return None
    return present(rom)
