"""Bounded original ROM data for m2c, resolved through the selected function's overlay reader."""
import re
import struct

MAX_SYMBOLS = 16
MAX_BYTES = 4096
MAX_TOTAL = 16384


def read(net, proj, fn):
    """Supply known-size tables/constants and terminated strings; never invent missing bytes or case targets."""
    syms = net.symbols(proj['id'])
    reader = None
    asm = fn['asm'] or ''
    out = ['.section .rodata']; used = 0
    instruction_addresses = {int(m,16) for m in re.findall(r'/\*\s*\w+\s+([0-9A-F]{8})\s+\w+\s*\*/', asm)}
    names = sorted(set(re.findall(r'%(?:hi|lo)\(([A-Za-z_]\w*)\)', asm)))
    count = 0
    endian = '>' if net.cfg(proj).get('big_endian', True) else '<'
    for name in names:
        symbol = syms.by_name.get(name)
        if not symbol:
            continue
        table = name.startswith('jtbl_') or symbol.kind == 'jtbl'
        string = name.startswith('STR_') or symbol.kind == 'string'
        constant = name.startswith(('RO_', 'FLT_', 'DBL_')) or symbol.kind == 'rodata'
        size = symbol.size or (4 if name.startswith('FLT_') else 8 if name.startswith('DBL_') else 0)
        if not (table or string or constant) or not 0 < size <= MAX_BYTES:
            continue
        if count >= MAX_SYMBOLS or used + size > MAX_TOTAL:
            break
        if reader is None:
            reader = net.reader(proj, fn)
        data = reader(symbol.address, size)
        if data is None or len(data) != size:
            continue
        if table:
            if len(data) % 4:
                continue
            # Keep the exact symbolic name in this overlay, even if another alias shares its RAM address.
            body = ['.word .L%08X' % w if w in instruction_addresses else '.word 0x%08X' % w
                    for (w,) in struct.iter_unpack(endian+'I', data)]
        elif string and b'\0' in data:
            value = data.split(b'\0',1)[0]
            if any(b < 32 or b >= 127 for b in value):
                continue
            escaped = value.decode('ascii').replace('\\', '\\\\').replace('"','\\"')
            body = ['.asciz "'+escaped+'"']
            tail = data[len(value)+1:]
            if tail:
                body.append('.byte '+', '.join('0x%02X' % b for b in tail))
        else:
            body = ['.byte '+', '.join('0x%02X' % b for b in data[i:i+16]) for i in range(0,len(data),16)]
        out.extend(['dlabel '+name, *body]); count += 1; used += len(data)
    return out
