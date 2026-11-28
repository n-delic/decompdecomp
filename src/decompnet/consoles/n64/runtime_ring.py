"""Recover a runtime program from an audited ring-buffer loader and observed RAM."""

import hashlib

from ...arch.mips_assets import read

_LIMIT = 8 * 1024 * 1024
# Full decoder body: byte flags, a 4 KiB ring starting at one, and a zero-pair terminator.
_LOADERS = {676: {'62369405c2df6573586b7b88f64bfadd82394320eea38f37b9848488ef0ec7f1'}}


def decode(source):
    """Return the output and consumed length of a bounded, terminated stream."""
    ring = bytearray(4096)
    cursor, at = 1, 0
    output = bytearray()
    try:
        while at < len(source) and len(output) <= _LIMIT:
            flags = source[at]
            at += 1
            for _ in range(8):
                if flags & 1:
                    values = (source[at],)
                    at += 1
                    length = 1
                else:
                    first, second = source[at], source[at + 1]
                    at += 2
                    offset = ((first & 0xf0) << 4) | second
                    length = (first & 15) + 2
                    if not offset and not (first & 15):
                        return bytes(output), at
                    values = None
                if len(output) + length > _LIMIT:
                    return None
                for index in range(length):
                    value = values[0] if values is not None else ring[(offset + index) & 0xfff]
                    output.append(value)
                    ring[cursor] = value
                    cursor = (cursor + 1) & 0xfff
                flags >>= 1
    except IndexError:
        return None
    return None


def find(rom, dumps, targets, occupied=(), check=lambda: None):
    """Require the exact loader, its constant source/destination, and matching loaded code."""
    if not targets:
        return []
    from .boot_gzip import _coalesce_boot_ranges

    candidates = {}
    ranges = _coalesce_boot_ranges(occupied)
    for lo, hi, base in ranges:
        if lo >= 0x10000 or not 0 <= lo < hi <= len(rom):
            continue
        check()
        end = min(hi, 0x20000)
        for event in read(base, rom[lo:end])['events']:
            if event.get('kind') != 'call' or not isinstance(event.get('target'), int):
                continue
            args = event.get('args') or []
            if len(args) < 2 or any(not arg or arg.get('kind') != 'constant' for arg in args[:2]):
                continue
            source, dest = (arg.get('value') for arg in args[:2])
            if (type(source) is not int or type(dest) is not int or not 0 <= source < len(rom)
                    or dest % 16 or not 0x80000400 <= dest < 0x80800000):
                continue
            target = event['target']
            loader = lo + target - base
            if not any(lo <= loader and loader + size <= end
                       and hashlib.sha256(rom[loader:loader + size]).hexdigest() in digests
                       for size, digests in _LOADERS.items()):
                continue
            decoded = decode(memoryview(rom)[source:source + _LIMIT * 2])
            if not decoded:
                continue
            payload, used = decoded
            if len(payload) < 0x10000 or dest + len(payload) > 0x80800000:
                continue
            if any(v < dest + len(payload) and dest < v + b - a for a, b, v in occupied):
                continue
            for dump in dumps:
                at = dest & 0x1fffffff
                if at + len(payload) > len(dump) or dump[at:at + 0x10000] != payload[:0x10000]:
                    continue
                calls = sorted(t for t in targets if dest <= t <= dest + len(payload) - 64
                               and dump[t & 0x1fffffff:(t & 0x1fffffff) + 64] == payload[t - dest:t - dest + 64])
                if calls:
                    row = dict(codec='ring_lzss', offset=source, size=used, expanded=len(payload),
                               vram=dest, calls=calls)
                    candidates[(source, dest)] = row
                    break
    return list(candidates.values()) if len(candidates) == 1 else []
