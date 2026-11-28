"""Identify independently called prefixes that share a following entry."""

from collections import Counter, defaultdict

# These instructions have no control transfer or delay slot. More complex
# prefixes remain visible to boundary review until they can be proven safe.
_STRAIGHT = frozenset(('addiu', 'lui', 'lwc1', 'add.s', 'lw', 'sw', 'sll', 'srl', 'addu', 'subu', 'sh', 'ori'))


def _straight(line):
    if line.mnemonic.lower() in _STRAIGHT:
        return True
    # A move between argument registers (OR with zero) is a common entry
    # shim into an independently called vector routine's shared tail.
    word = line.word
    return (line.mnemonic.lower() == 'or' and word >> 26 == 0 and word & 0x7ff == 0x25
            and (word >> 16) & 31 == 0 and 4 <= (word >> 21) & 31 <= 7
            and 4 <= (word >> 11) & 31 <= 7)


def intentional(functions, console, segments):
    """Return evidence for unambiguous calls to both adjacent MIPS entries.

    This does not classify code as handwritten assembly or make a C match: it
    records that the extent is an intentional entry into a shared tail.
    """
    if console.arch != 'mips':
        return {}
    counts = Counter(fn.vram for fn in functions)
    by_address = {fn.vram: fn for fn in functions if counts[fn.vram] == 1}
    calls, transfers = defaultdict(set), set()
    for fn in functions:
        for line in fn.lines:
            op = line.word >> 26
            if op in (2, 3):
                dest = ((line.vram + 4) & 0xf0000000) | ((line.word & 0x3ffffff) << 2)
                transfers.add(dest)
                if op == 3:
                    calls[dest].add(fn.vram)
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (line.word >> 21) & 31 == 8:
                delta = line.word & 0xffff
                transfers.add(line.vram + 4 + (delta - 0x10000 if delta & 0x8000 else delta) * 4)
    result = {}
    for fn in functions:
        if fn.piece or not 0 < fn.size <= 256 or counts[fn.vram] != 1 or fn.vram not in calls:
            continue
        owners = [segment for segment in segments if segment.contains(fn.vram, fn.size + 4)]
        if (len(owners) != 1 or owners[0].name != fn.segment
                or owners[0].vram - owners[0].rom_start != fn.vram - fn.rom):
            continue
        if any(not _straight(line) for line in fn.lines) or any(
                at in transfers for at in range(fn.vram + 4, fn.vram + fn.size, 4)):
            continue
        tail = by_address.get(fn.vram + fn.size)
        if (tail is None or tail.piece or tail.size < 8 or tail.vram not in calls or tail.rom != fn.rom + fn.size
                or tail.segment != fn.segment or not owners[0].contains(tail.vram, tail.size)
                or tail.rom + tail.size > owners[0].rom_end
                or not calls[fn.vram] - {fn.vram, tail.vram}
                or not calls[tail.vram] - {fn.vram, tail.vram} or console.boundary_reason(b''.join(
                    line.word.to_bytes(4, 'big' if console.big_endian else 'little') for line in tail.lines))):
            continue
        result[fn.name] = {'tail': tail.name, 'tail_vram': tail.vram,
                           'evidence': 'Both adjacent entries have independent direct calls; the prefix falls through into the complete shared tail.'}
    return result
