"""Resident raw-deflate programs identified by a boot-loaded address header."""

import struct
import zlib

from ...arch.mips_assets import read

_LIMIT = 4 * 1024 * 1024


def _constant(value):
    return value['value'] if value and value.get('kind') == 'constant' else None


def find(rom, boot_ranges):
    """Bind a compressed ROM range to the header loaded and executed by boot code."""
    from .code import _looks_like_code

    matches = {}
    for start, end, vram in boot_ranges:
        if start >= 0x10000:
            continue
        stop = min(end, 0x10000)
        events = read(vram, rom[start:stop])['events']
        calls = [event for event in events if event['kind'] == 'call']
        loads = {event['instruction']: event for event in events if event['kind'] == 'load'}
        for dma in calls:
            if dma['target'] is None:
                continue
            header, scratch, size = (_constant(arg) for arg in dma['args'][:3])
            if (header is None or scratch is None or size != 16 or not 0x10000 <= header <= len(rom)-16
                    or not 0x80000000 <= scratch <= 0x807ffff0 or scratch % 4):
                continue
            dest, entry, bss_start, bss_end = struct.unpack_from('>4I', rom, header)
            if not (0x80000000 <= dest <= entry < bss_start <= bss_end <= 0x80800000
                    and not (dest | entry | bss_start | bss_end) & 3 and bss_start-dest <= _LIMIT):
                continue
            for inflate in calls:
                if inflate['target'] is None or len(inflate['args']) < 3:
                    continue
                source, target, length = inflate['args'][:3]
                source, length = _constant(source), _constant(length)
                if (inflate['target'] is None or inflate['block'] != dma['block']
                        or not 0 < inflate['instruction'] - dma['instruction'] <= 0x80
                        or source != header + 16 or length is None or not 0 < length <= _LIMIT
                        or source + length > len(rom) or not target or target.get('kind') != 'memory'):
                    continue
                load = loads.get(target['value'])
                if (not load or load['block'] != dma['block'] or load['width'] != 4
                        or _constant(load['place']) != scratch):
                    continue
                # The boot code loads the entry word from the same header and
                # immediately jumps through that exact register.
                executed = False
                for call in calls:
                    if (call['target'] is not None or call['block'] != dma['block']
                            or not 0 < call['instruction'] - inflate['instruction'] <= 0x80):
                        continue
                    at = start + call['instruction'] - vram
                    if not start <= at <= stop - 4:
                        continue
                    jump = int.from_bytes(rom[at:at+4], 'big')
                    if jump & 0xfc1f07ff != 9:
                        continue
                    register = (jump >> 21) & 31
                    for earlier in loads.values():
                        if (earlier['block'] != dma['block'] or earlier['width'] != 4
                                or _constant(earlier['place']) != scratch + 4
                                or not 0 < call['instruction'] - earlier['instruction'] <= 16):
                            continue
                        load_at = start + earlier['instruction'] - vram
                        if start <= load_at <= stop - 4:
                            word = int.from_bytes(rom[load_at:load_at+4], 'big')
                            if word >> 26 == 0x23 and (word >> 16) & 31 == register:
                                executed = True
                if not executed:
                    continue
                decoder = zlib.decompressobj(-15)
                try:
                    data = decoder.decompress(rom[source:source+length], bss_start-dest+1)
                except zlib.error:
                    continue
                if (not decoder.eof or len(data) != bss_start-dest or len(decoder.unused_data) > 32
                        or not _looks_like_code(data)):
                    continue
                returns = [at for at in range(0, len(data)-7, 4) if data[at:at+4] == b'\x03\xe0\x00\x08']
                if not returns or (text_end := returns[-1] + 8) <= entry-dest:
                    continue
                if any(dest < base + stop_at - begin and base < bss_start for begin, stop_at, base in boot_ranges):
                    continue
                matches[header, dest, source] = ((dest, data[:text_end], data[text_end:]),)
    return next(iter(matches.values())) if len(matches) == 1 else _table_find(rom, boot_ranges)


def _table_find(rom, boot_ranges):
    """Find a named boot-table program copied through a verified mapped alias."""
    from .code import _looks_like_code

    boot = b''.join(rom[start:end] for start, end, _ in boot_ranges if start < 0x10000)
    # This boot configures a TLB alias at 0xe0000000 before jumping to the
    # corresponding cached address. Without that mapping the table's virtual
    # destinations cannot safely be interpreted as physical code addresses.
    if b'\x3c\x06\xe0\x00' not in boot or b'\x3c\x04\xa4\x60' not in boot:
        return None
    calls = set()
    for start, end, vram in boot_ranges:
        for at in range(start, min(end, 0x10000)-3, 4):
            word = int.from_bytes(rom[at:at+4], 'big')
            if word >> 26 == 3:
                calls.add(((vram+at-start+4) & 0xf0000000) | ((word & 0x3ffffff) << 2))
    matches = {}
    marker = b'game\0'+bytes(11)
    at = 0
    while (at := rom.find(marker, at, min(len(rom), 0x10000))) >= 0:
        record = at
        at += len(marker)
        if record+40 > len(rom):
            continue
        source, stored, virtual, _, bss, _ = struct.unpack_from('>6I', rom, record+16)
        if (not 0 < stored <= _LIMIT or source < 0x1000 or source+stored > len(rom)
                or not 0xe0000000 <= virtual < bss <= 0xe0800000
                or (virtual | bss) & 3 or bss-virtual > _LIMIT):
            continue
        table_ok = False
        for index in range(128):
            header = record-index*48-28
            if header < 0:
                break
            count = int.from_bytes(rom[header:header+4], 'big')
            if not index < count <= 128 or header+28+count*48 > len(rom):
                continue
            names = [bytes(rom[header+28+i*48:header+44+i*48]).split(b'\0', 1)[0] for i in range(count)]
            if len(set(names)) == count and all(name and all(32 <= c < 127 for c in name) for name in names):
                table_ok = True
                break
        if not table_ok:
            continue
        dest = 0x80000000 + virtual-0xe0000000
        end = dest+bss-virtual
        if not any(dest <= call < end for call in calls):
            continue
        decoder = zlib.decompressobj(-15)
        try:
            data = decoder.decompress(rom[source:source+stored], end-dest+1)
        except zlib.error:
            continue
        if not decoder.eof or len(data) != end-dest or len(decoder.unused_data) > 32 or not _looks_like_code(data):
            continue
        returns = [i for i in range(0, len(data)-7, 4) if data[i:i+4] == b'\x03\xe0\x00\x08']
        if not returns:
            continue
        text_end = returns[-1]+8
        if any(dest < base+stop-start and base < end for start, stop, base in boot_ranges):
            continue
        matches[record, source, dest] = ((dest, data[:text_end], data[text_end:]),)
    return next(iter(matches.values())) if len(matches) == 1 else None
