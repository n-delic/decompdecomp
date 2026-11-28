"""Recover a boot program from a literal/backreference cartridge directory."""

import struct

from ...arch.mips_assets import read

_LIMIT = 4 * 1024 * 1024


def decode(source, expected):
    """Decode byte-counted literals and little-endian backward copies."""
    if not 0 < expected <= _LIMIT or len(source) < 6:
        return None
    # The five-byte stream prefix is consumed by the boot decoder itself.
    at, out = 5, bytearray()
    while at < len(source):
        command = source[at]
        at += 1
        if not command:
            return bytes(out) if at == len(source) and len(out) == expected else None
        count = command & 0x7f
        if len(out)+count > expected:
            return None
        if command & 0x80:
            if at+count > len(source):
                return None
            out.extend(source[at:at+count])
            at += count
        else:
            if at+2 > len(source):
                return None
            distance = source[at] | source[at+1] << 8
            at += 2
            if not 0 < distance <= len(out):
                return None
            for _ in range(count):
                out.append(out[-distance])
    return None


def find(rom, boot_ranges):
    """Require the boot's directory load, decoder call and indirect entry jump."""
    from .code import _looks_like_code

    found = []
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom) or hi-lo < 16:
            continue
        events = read(base, rom[lo:hi])['events']
        loads = {e['instruction']: e for e in events if e['kind'] == 'load'}
        calls = [e for e in events if e['kind'] == 'call']
        for call in calls:
            args = call.get('args') or []
            if len(args) < 2 or not args[0] or not args[1] or call.get('target') is None:
                continue
            source, target = args[:2]
            if source.get('kind') != 'memory' or target.get('kind') != 'constant':
                continue
            load = loads.get(source.get('value'))
            dest = target['value']
            if not load or load.get('width') != 4 or not 0x80000400 <= dest < 0x80400000 or dest % 16:
                continue
            address = source.get('offset')
            if not isinstance(address, int):
                continue
            directory = lo+address-base
            if not 0 <= directory <= len(rom)-16 or directory % 4:
                continue
            first, expanded, packed_flag, next_offset = struct.unpack_from('>4I', rom, directory)
            packed = packed_flag & 0x7fffffff
            if (not packed_flag & 0x80000000 or not 16 <= first <= 4096 or first % 8
                    or not 0x1000 <= expanded <= _LIMIT or not 0 < packed <= _LIMIT
                    or next_offset != first+packed or directory+next_offset > len(rom)
                    or dest+expanded > 0x80800000):
                continue
            decoder = lo+call['target']-base
            signature = (0x90860005, 0x00005021, 0x10c0003b, 0x240b0005)
            if not lo <= decoder <= hi-16 or struct.unpack_from('>4I', rom, decoder) != signature:
                continue
            # The boot establishes the destination register, rounds its entry
            # to the first 64 bytes after the output base, and jumps through it.
            words = [w for w, in struct.iter_unpack('>I', rom[lo:hi-(hi-lo)%4])]
            init = (0x3c100000 | (dest >> 16), 0x36100000 | (dest & 0xffff),
                    0x26100047, 0x2402fff8, 0x02028024, 0x0200f809)
            positions = []
            for word in init:
                try:
                    positions.append(words.index(word, positions[-1]+1 if positions else 0))
                except ValueError:
                    break
            call_index = (call['instruction']-base)//4
            if len(positions) != len(init) or not positions[1] < call_index < positions[2]:
                continue
            if not any(c['target'] is None and c['instruction'] == base+4*positions[-1] for c in calls):
                continue
            data = decode(rom[directory+first:directory+next_offset], expanded)
            if not data or any(data[:64]) or not _looks_like_code(data[64:]):
                continue
            returns = [i for i in range(64, len(data)-7, 4) if data[i:i+4] == b'\x03\xe0\x00\x08']
            if returns:
                end = returns[-1]+8
                found.append(((dest, data[:end], data[end:]),))
    return found[0] if len(found) == 1 else None
