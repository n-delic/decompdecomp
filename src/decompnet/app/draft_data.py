"""Bounded original ROM data for m2c, resolved through the selected function's overlay reader."""
import re
import struct
from types import SimpleNamespace

MAX_SYMBOLS = 16  # constants and strings; jump tables don't count (below)
MAX_BYTES = 4096
MAX_TOTAL = 16384
MAX_TABLES = 64
MAX_CASES = 256


def case_target(word, instruction_addresses):
    """The function address a jump-table word names, or None. A game that patches its tables when it loads a
    segment keeps them in the ROM without the KSEG0 bit (`0x0025A1D8` for `0x8025A1D8`): 79 of a 2,465-function
    N64 game's open functions had no draft because no word of their table looked like one of their own
    instructions. The bit is added only when that makes a real instruction of this function, so a stray word is
    still not a case."""
    if word in instruction_addresses:
        return word
    high = word | 0x80000000
    return high if not word & 0x80000000 and high in instruction_addresses else None


def jump_table_extent(name, reader, instruction_addresses, endian):
    """(address, size) of a jump table the symbol list doesn't have: its address is in its name (`jtbl_800E1DE8`)
    and it runs as long as its words are addresses of this function's own instructions. Nothing is invented: a word
    that is not a case target ends the table, and fewer than two cases is not a table. m2c can't resolve a `jr`
    without it, so a function whose table was missing from the symbols (Penny Racers: 40 of its 50 functions without
    a draft) got no draft at all."""
    match = re.fullmatch(r'jtbl_([0-9A-Fa-f]{8})', name)
    if not match:
        return None
    address = int(match[1], 16)
    cases = 0
    while cases < MAX_CASES:
        word = reader(address + 4 * cases, 4)
        if word is None or len(word) != 4 or case_target(struct.unpack(endian + 'I', word)[0], instruction_addresses) is None:
            break
        cases += 1
    return (address, 4 * cases) if cases >= 2 else None


def beside_code_reader(rom, fn, reader):  # (`rom`: the bytes, or a function that returns them, read only when needed)
    """`reader`, then the ROM at the function's own offset between ROM and memory address for what no segment or
    loaded image covers: a game whose splat config lists only code has its jump tables and constants in the data
    that follows (Army Men: 100 functions without a draft, their tables sitting after the last code segment). Only
    for m2c's draft, never for the judge: a jump table is still checked against the function's own instruction
    addresses (`jump_table_extent`), so a wrong guess for an overlay finds no table rather than a false one."""
    try:
        vram, offset = fn['vram'], fn['rom_offset']
    except (KeyError, IndexError):  # (a row without them)
        return reader
    if not isinstance(vram, int) or not isinstance(offset, int):
        return reader
    delta = offset - vram

    def read(address, size):
        found = reader(address, size)
        if found is None and address + delta >= 0:
            data = rom() if callable(rom) else rom
            if address + delta + size <= len(data):
                return data[address + delta:address + delta + size]
        return found
    return read


def delta_readers(net, proj, fn):
    """Readers of the ROM at each segment's own ROM-to-address offset, for a table in data no segment lists: a game
    whose boot image holds its data after the code (a jump table at 0x800AD078, 79 functions) has them at the boot
    segment's offset, not at the function's overlay's. Each reader is wrong for most addresses, which is safe: a table
    is only accepted when its words are this function's own instruction addresses (`jump_table_extent`)."""
    try:
        data, segments = net._rom(proj)
    except Exception:  # (no ROM to read: nothing to offer)
        return []
    deltas = dict.fromkeys(s.rom_start - s.vram for s in segments)

    def reader(delta):
        def read(address, size):
            at = address + delta
            return data[at:at + size] if 0 <= at and at + size <= len(data) else None
        return read
    return [reader(delta) for delta in deltas]


def read(net, proj, fn):
    """Supply known-size tables/constants and terminated strings; never invent missing bytes or case targets."""
    syms = net.symbols(proj['id'])
    reader = None
    asm = fn['asm'] or ''
    out = ['.section .rodata']; used = 0
    instruction_addresses = {int(m,16) for m in re.findall(r'/\*\s*\w+\s+([0-9A-F]{8})\s+\w+\s*\*/', asm)}
    names = sorted(set(re.findall(r'%(?:hi|lo)\(([A-Za-z_]\w*)\)', asm)))
    # Tables first, outside the constants' limits: m2c can't decompile a function with any `jr` whose table is
    # missing, while a missing constant only costs a named global. A large state machine (a 4,399-instruction N64
    # function with 18 tables) got 4 of them after its constants and no draft at all.
    names.sort(key=lambda name: not name.startswith('jtbl_'))
    count = tables = 0
    beside = []
    found_readers = {}

    def table_reader():  # (only tables, which are checked against the function's own addresses)
        if not beside:
            beside.append(beside_code_reader(lambda: net._rom(proj)[0], fn, reader))
        return beside[0]
    endian = '>' if net.cfg(proj).get('big_endian', True) else '<'
    for name in names:
        symbol = syms.by_name.get(name)
        if (not symbol or not symbol.size) and name.startswith('jtbl_'):
            if reader is None:
                reader = net.reader(proj, fn)
            for candidate in (table_reader(), *delta_readers(net, proj, fn)):
                if extent := jump_table_extent(name, candidate, instruction_addresses, endian):
                    symbol = SimpleNamespace(address=extent[0], size=extent[1], kind='jtbl')
                    found_readers[name] = candidate
                    break
        if not symbol:
            continue
        table = name.startswith('jtbl_') or symbol.kind == 'jtbl'
        string = name.startswith('STR_') or symbol.kind == 'string'
        constant = name.startswith(('RO_', 'FLT_', 'DBL_')) or symbol.kind == 'rodata'
        size = symbol.size or (4 if name.startswith('FLT_') else 8 if name.startswith('DBL_') else 0)
        if not (table or string or constant) or not 0 < size <= MAX_BYTES:
            continue
        if table and tables >= MAX_TABLES:
            continue
        if not table and (count >= MAX_SYMBOLS or used + size > MAX_TOTAL):
            break
        if reader is None:
            reader = net.reader(proj, fn)
        data = (found_readers.get(name) or (table_reader() if table else reader))(symbol.address, size)
        if data is None or len(data) != size:
            continue
        if table:
            if len(data) % 4:
                continue
            # Keep the exact symbolic name in this overlay, even if another alias shares its RAM address.
            words = [w for (w,) in struct.iter_unpack(endian+'I', data)]
            while len(words) > 2 and words[-1] == 0:
                words.pop()  # alignment padding the data file counted into the table; m2c rejects it as a case
            body = ['.word .L%08X' % case_target(w, instruction_addresses) if case_target(w, instruction_addresses) else '.word 0x%08X' % w
                    for w in words]
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
        out.extend(['dlabel '+name, *body])
        if table:
            tables += 1
        else:
            count += 1; used += len(data)
    return out
