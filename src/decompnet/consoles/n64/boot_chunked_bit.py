"""Recover an audited boot program from independent MSB-first LZ bit chunks."""

import hashlib
import struct

LOADER_DIGEST = '9f4eee2e007ab9c5a521a86f13eb7590148c4e6607650eccf4092e0660f6ad68'
OUTPUT_DIGEST = '10d91d2c6916496106b399cc05b6ee0663d10a3ae9fd8003cad4936ad5476b71'
CODE_START = 0x50
CODE_END = 0x550b4
EXTRA_PROGRAMS = (
    # Directory index, ROM-relative offset, output size, stored size, load address,
    # text start/end, and full decoded-image digest; the text matches observed RAM.
    (1, 0x33748, 0x909b0, 0x47dd4, 0x8007a860, 0xed8, 0x7f858,
     '59f8ea8457eebd21ff8936094b0ee5fb875158644e46652e6da8ba30d2249b0f'),
    (2, 0x7b51c, 0x90810, 0x49cb8, 0x8007a860, 0x101f0, 0x82c2c,
     'aa61544efd5d4fdead01fe0064c4be710d3675a763e7ec86b2702a4795a8d785'),
    (4, 0xc51d8, 0x33310, 0x1a9b0, 0x8010b210, 0x8, 0x33310,
     '379e415c25f7841e3053233aecc6afc146dee3ec62f31a32502df74f97decc3d'),
    (5, 0xdfb88, 0x337e0, 0x1a6c8, 0x8010b210, 0x1048, 0x2be04,
     '96d59f2c1077f726937795b45ac0a7fa10fa1d5a0942c5fb589ac7dd592eb451'),
)
MAX_OUTPUT = 4 * 1024 * 1024


def drop_impossible_bss(segments):
    """Discard a mistaken size larger than all N64 RDRAM, keeping valid sizes."""
    for segment in segments:
        if (isinstance(segment, dict) and segment.get('type') == 'code'
                and isinstance(segment.get('bss_size'), int) and segment['bss_size'] > 0x800000):
            segment.pop('bss_size')


def code_start(vram, text):
    if vram == 0x80000400:
        return CODE_START
    return next((start for _, _, _, _, address, start, end, _ in EXTRA_PROGRAMS
                 if address == vram and end == len(text)), None)


def decode(source, expected):
    """Decode bounded 4 KiB input chunks with a shared 64 KiB history."""
    if not source or not 0 < expected <= MAX_OUTPUT:
        return None
    out = bytearray()
    try:
        for start in range(0, len(source), 0x1000):
            chunk = source[start:start + 0x1000]
            pos = 0

            def bits(count, chunk=chunk):
                nonlocal pos
                if pos + count > len(chunk) * 8:
                    raise ValueError('truncated bit chunk')
                value = 0
                for _ in range(count):
                    value = (value << 1) | ((chunk[pos // 8] >> (7 - pos % 8)) & 1)
                    pos += 1
                return value

            if not out:
                if bits(8) != 3:
                    return None
                for _ in range(4):
                    bits(8)
            while len(chunk) * 8 - pos >= 8 and len(out) < expected:
                if bits(1):
                    count = bits(7)
                    if len(out) + count > expected:
                        return None
                    for _ in range(count):
                        out.append(bits(8))
                    continue
                prefix = 0
                while prefix < 7 and not bits(1):
                    prefix += 1
                count = ((1 << prefix) | bits(prefix) if prefix else 1) + 2
                width = min(max(1, len(out).bit_length()), 16)
                threshold = (1 << width) - 1 - min(len(out), 0x10000)
                if threshold == -1:
                    distance = bits(width)
                else:
                    distance = bits(width - 1)
                    if distance >= threshold:
                        distance = ((distance << 1) | bits(1)) - threshold
                if not 0 < distance <= len(out) or len(out) + count > expected:
                    return None
                for _ in range(count):
                    out.append(out[-distance])
        return bytes(out) if len(out) == expected else None
    except (ValueError, IndexError):
        return None


def present(rom):
    """Identify the audited loader and exact output before placing its code."""
    if hashlib.sha256(rom[0x1000:0x16e0]).hexdigest() != LOADER_DIGEST or len(rom) < 0x34e88:
        return None
    offset, expanded, flags = struct.unpack_from('>III', rom, 0x1740)
    if (offset, expanded, flags) != (0x54, 0x61578, 0x800336f4):
        return None
    source = 0x1740 + offset
    packed = flags & 0x7fffffff
    if source + packed > len(rom):
        return None
    data = decode(rom[source:source + packed], expanded)
    if data is None or hashlib.sha256(data).hexdigest() != OUTPUT_DIGEST:
        return None
    programs = [(0x80000400, data[:CODE_END], data[CODE_END:])]
    for index, offset, expanded, packed, vram, start, end, digest in EXTRA_PROGRAMS:
        if 0x1740 + 12 * (index + 1) > len(rom):
            return None
        if struct.unpack_from('>III', rom, 0x1740 + 12 * index) != (offset, expanded, 0x80000000 | packed):
            return None
        source = 0x1740 + offset
        if source + packed > len(rom):
            return None
        data = decode(rom[source:source + packed], expanded)
        if (data is None or not 0 <= start < end <= len(data)
                or hashlib.sha256(data).hexdigest() != digest):
            return None
        programs.append((vram, data[:end], data[end:]))
    return tuple(programs)


def find(rom, boot_ranges):
    from .boot_gzip import _coalesce_boot_ranges

    boot_ranges = _coalesce_boot_ranges(boot_ranges)
    if not any(start <= 0x1000 and end >= 0x16e0 and base + 0x1000 - start == 0x80200400
               for start, end, base in boot_ranges):
        return None
    return present(rom)
