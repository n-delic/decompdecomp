"""Recover a directly entered bootstrap and its closed, directly called helpers."""
import struct

import yaml

from ...arch.mips_gaps import extent

_LIMIT = 0x4000
_MAX_FUNCTIONS = 64


def recover(config, image, log=print):
    """Keep skipped boot metadata as data and map only proven startup control flow."""
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    rows = cfg.get('segments', [])
    entry = next((r for r in rows if isinstance(r, dict) and r.get('name') == 'entry'), None)
    main = next((r for r in rows if isinstance(r, dict) and r.get('name') == 'main'), None)
    if not entry or not main or entry.get('start') != 0x1000 or main.get('start') != 0x1010:
        return False
    if (entry.get('subsegments') != [[0x1000, 'hasm']] or main.get('follows_vram') != 'entry'
            or main.get('subsegments') != [[0x1010, 'c', 'main']] or main.get('bss_size')
            or len(image) < _LIMIT):
        return False
    index = rows.index(main)
    if index+1 >= len(rows) or not isinstance(rows[index+1], dict):return False
    raw = rows[index+1]
    if raw.get('type') != 'bin' or raw.get('start') != 0x1020:return False
    next_start = next((r.get('start') for r in rows[index+2:] if isinstance(r, dict) and isinstance(r.get('start'), int)), len(image))
    bound = min(_LIMIT, next_start, len(image))
    address = entry.get('vram')
    if not isinstance(address, int) or address % 4:return False
    jump, delay, pad1, pad2 = struct.unpack_from('>4I', image, 0x1000)
    if jump >> 26 != 2 or delay or pad1 or pad2:return False
    target = ((address+4)&0xf0000000) | ((jump&0x3ffffff)<<2)
    root = 0x1000+target-address
    if root < 0x1020 or root % 4 or root+32 > bound:return False
    # Do not remove a skipped word if it is named by an absolute pointer or a
    # direct J/JAL anywhere in the image. Ambiguity stays available for review.
    for virtual in range(address+0x10, target, 4):
        if virtual.to_bytes(4, 'big') in image:return False
        for op in (2, 3):
            if ((op<<26)|((virtual>>2)&0x3ffffff)).to_bytes(4, 'big') in image:return False
    mapping = address-0x1000
    pending, found = [root], {}
    while pending:
        at = pending.pop()
        if at in found:continue
        if len(found) >= _MAX_FUNCTIONS or not root <= at < bound:return False
        info = extent(image, at, bound, at+mapping, 'big', cpu='vr4300')
        size = info['extent_bytes']
        if size < 8 or info['step_limit'] or info['decoded_instructions']*4 != size:return False
        if at == root:
            transfers = info['unresolved_transfers']
            if (info['return_sites'] or len(transfers) != 1 or info['jump_tables']
                    or transfers[0]['address'] != at+mapping+size-8):return False
        elif not info['direct_flow_closed']:
            return False
        end = at+size
        if end > bound or any(at < b and a < end for a, b in found.items()):return False
        found[at] = end
        for pos in range(at, end, 4):
            word = int.from_bytes(image[pos:pos+4], 'big')
            op = word >> 26
            if op in (2, 3):
                dest = (((pos+mapping+4)&0xf0000000)|((word&0x3ffffff)<<2))-mapping
                if op == 3:
                    if not root <= dest < bound:return False
                    pending.append(dest)
                elif not at <= dest < end:return False
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21)&31 == 8:
                delta = word&0xffff
                dest = pos+4+(delta-0x10000 if delta&0x8000 else delta)*4
                if not at <= dest < end:return False
    if len(found) < 2:return False
    segments = [dict(type='bin', start=0x1010)]
    cursor = 0x1010
    for start, end in sorted(found.items()):
        if start > cursor and cursor != 0x1010:segments.append(dict(type='bin', start=cursor))
        segments.append(dict(name=f'boot_{start:X}', type='code', start=start, vram=start+mapping,
                             subsegments=[[start, 'c', f'boot_{start:X}']]))
        cursor = end
    if cursor < next_start:segments.append(dict(type='bin', start=cursor))
    rows[index:index+2] = segments
    # Splat otherwise gives the direct-jump entry a local .L name, which is not
    # a C identifier. Keep any existing game symbol for the same address.
    from ...rom.split import _add_symbols

    _add_symbols(config.parent / 'symbol_addrs.txt', [
        f'boot_{start+mapping:08X} = 0x{start+mapping:08X}; // type:func' for start in sorted(found)])
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    log(f'Recovered {len(found)} directly entered boot routines; skipped metadata remains data.')
    return True
