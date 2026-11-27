"""Recover linked text from consecutive MIPS relocation-section fragments.

Some cartridges concatenate independently linked overlays. A long run of their
text can resemble a single mapping even though each trailer is omitted when the
next overlay is linked. Treating it as one segment cuts calls into the middle
of unrelated functions. A valid relocation trailer supplies exact file and text
bounds; relocated calls to frame starts independently establish the link base.
"""
import struct
from collections import Counter

from ...arch.mips import JR_RA, is_frame
from ...arch.mips_code_recovery import called_extent, leaf_extent
from ...arch.mips_references import references

_MAX_TRAILER = 0x10000
_MAX_PAIRS = 1_000_000


def _base(image, start, text_size, relocations):
    words = struct.unpack_from(f'>{text_size // 4}I', image, start)
    targets = {0x80000000 | ((words[(r & 0xffffff) // 4] & 0x3ffffff) << 2)
               for r in relocations if r >> 30 == 1 and (r >> 24) & 63 == 4}
    entries = [4*i for i, word in enumerate(words)
               if is_frame(word) or word and i >= 2 and words[i-2] == JR_RA]
    if len(targets) < 3 or len(targets) * len(entries) > _MAX_PAIRS:
        return None
    scores = Counter(target-offset for target in targets for offset in entries
                     if (target-offset) % 16 == 0 and 0x80000400 <= target-offset < 0x90000000)
    ranked = scores.most_common(2)
    if not ranked:
        return None
    base, score = ranked[0]
    if score < 3 or len(ranked) > 1 and score < 3 * ranked[1][1]:
        return None
    return base


def _record(image, end):
    """Read one footer only if its section sizes and every relocation agree."""
    trailer = struct.unpack_from('>I', image, end-4)[0]
    if not 24 <= trailer <= _MAX_TRAILER or trailer % 16 or end-trailer < 0x1000:
        return None
    section = end-trailer
    text, data, rodata, bss, count = struct.unpack_from('>5I', image, section)
    start = section-text-data-rodata
    if (start < 0x1000 or (start | text | data | rodata) % 16 or text > 0x40000
            or bss > 0x100000 or count > (trailer-24)//4):
        return None
    relocations = struct.unpack_from(f'>{count}I', image, section+20)
    sizes = (text, data, rodata)
    for r in relocations:
        section_id, kind, offset = r >> 30, (r >> 24) & 63, r & 0xffffff
        if (section_id not in (1, 2, 3) or kind not in (2, 4, 5, 6) or offset % 4
                or offset+4 > sizes[section_id-1]):
            return None
        if kind == 4:
            if section_id != 1 or struct.unpack_from('>I', image, start+offset)[0] >> 26 not in (2, 3):
                return None
    if any(image[section+20+count*4:end-4]):
        return None
    return start, end, text, relocations


def _supports_base(image, record, base):
    """Corroborate a predicted base with a relocated call and an independent function pointer."""
    start, end, text, relocations = record
    if not text or not 0x80000400 <= base < base+text <= 0x90000000:
        return False
    calls = {0x80000000 | ((struct.unpack_from('>I', image, start+(r & 0xffffff))[0] & 0x3ffffff) << 2)
             for r in relocations if r >> 30 == 1 and (r >> 24) & 63 == 4}
    lows = {start+(r & 0xffffff) for r in relocations if r >> 30 == 1 and (r >> 24) & 63 == 6}
    pointers = {target for target, at, kind in references(image, start, start+text, base)
                if kind == 'address' and at in lows}

    def closed(target):
        if target % 4 or not base <= target < base+text:
            return False
        at = start+target-base
        return bool(called_extent(image, at, start+text, target, cpu='vr4300')
                    or leaf_extent(image, at, start+text, target, cpu='vr4300'))

    if any(call != pointer and closed(call) and closed(pointer) for call in calls for pointer in pointers):
        return True
    # A minimal fragment may contain only a relocated function-pointer getter
    # and its return-only target. Its recorded pointer must name that exact leaf.
    if text == 32 and base+16 in pointers:
        words = struct.unpack_from('>8I', image, start)
        return (words[0] >> 16 == 0x3c02 and words[1:3] == (0xafa40000, JR_RA)
                and words[3] >> 16 == 0x2442 and words[4:6] == (JR_RA, 0xafa40000)
                and words[6:] == (0, 0) and closed(base+16))
    return False


def _bridge(image, checked):
    """Fill a run only when independent bases agree with every intervening file and BSS size."""
    result = list(checked)
    anchors = [i for i, (_, _, _, base) in enumerate(result) if base is not None]
    for left, right in zip(anchors, anchors[1:]):
        if right == left+1:
            continue
        base = result[left][3]
        inferred = []
        for i in range(left, right):
            start, end, _, _ = result[i]
            trailer = struct.unpack_from('>I', image, end-4)[0]
            bss = struct.unpack_from('>I', image, end-trailer+12)[0]
            base += end-start+bss
            if not 0x80000400 <= base < 0x90000000:
                break
            inferred.append(base)
        else:
            if base == result[right][3]:
                for i, address in enumerate(inferred[:-1], left+1):
                    start, end, text, _ = result[i]
                    result[i] = (start, end, text, address)
    # At a chain's ends there is no second base. Each extrapolated file must instead
    # contain two different, relocation-backed references to independently closed bodies.
    anchors = [i for i, (_, _, _, base) in enumerate(result) if base is not None]
    for step, first in ((1, anchors[-1]), (-1, anchors[0])):
        index = first
        while 0 <= index+step < len(result):
            following = index+step
            start, end, text, mapped = result[following]
            if mapped is not None:
                break
            previous = result[index]
            if step == 1:
                a, b, _, current = previous
                trailer = struct.unpack_from('>I', image, b-4)[0]
                candidate = current+b-a+struct.unpack_from('>I', image, b-trailer+12)[0]
            else:
                trailer = struct.unpack_from('>I', image, end-4)[0]
                candidate = previous[3]-(end-start)-struct.unpack_from('>I', image, end-trailer+12)[0]
            record = _record(image, end)
            if record is None or not _supports_base(image, record, candidate):
                break
            result[following] = (start, end, text, candidate)
            index = following
    return result


def discover(image, taken=(), check=lambda: None):
    """Return proven text mappings and file extents in chains of three trailers.

    A single coincidental footer is insufficient: each chain also needs two
    independently call-validated bases. A file without enough calls retains its
    existing mapping, but its recorded data and relocation table stay data.
    """
    records = []
    for end in range(0x1010, len(image)+1, 16):
        if end % 0x10000 == 0:
            check()
        if record := _record(image, end):
            start, _, _, _ = record
            if not any(a < end and start < b for a, b, _ in taken):
                records.append(record)
    ordered = sorted(records)
    chains, chain = [], []
    for record in ordered:
        if chain and chain[-1][1] != record[0]:
            if len(chain) >= 3:
                chains.append(chain)
            chain = []
        chain.append(record)
    if len(chain) >= 3:
        chains.append(chain)
    result = []
    for chain in chains:
        checked = []
        for start, end, text, relocations in chain:
            check()
            checked.append((start, end, text, _base(image, start, text, relocations)))
        if sum(base is not None for _, _, _, base in checked) >= 2:
            result.extend(_bridge(image, checked))
    return result


def replace(found, records):
    """Replace only proven files; retain all original bytes outside their text."""
    result = []
    for start, end, base in found:
        pieces = [(start, end)]
        for lo, hi, text, mapped in records:
            # If the link address is unknown, retain only the old text mapping.
            cut = lo if mapped is not None else lo+text
            pieces = [(a, b) for x, y in pieces for a, b in ((x, min(y, cut)), (max(x, hi), y)) if a < b]
        result.extend((a, b, base+a-start) for a, b in pieces)
    result.extend((start, start+text, base) for start, _, text, base in records if base is not None and text)
    return sorted(result)
