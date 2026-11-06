"""Small aligned MIPS constant differences that can guide a bounded C repair."""

from collections import Counter

IMMEDIATE = {8, 9, 10, 11, 12, 13, 14, 32, 33, 35, 36, 37, 40, 41, 43}
UNSIGNED = {12, 13, 14}


def changes(left, right, relocated, big):
    if not left or len(left) != len(right) or len(left) % 4:
        return []
    order = 'big' if big else 'little'
    evidence = Counter()
    for offset in range(0, len(left), 4):
        x = int.from_bytes(left[offset:offset + 4], order)
        y = int.from_bytes(right[offset:offset + 4], order)
        if x == y or offset in relocated:
            continue
        op = x >> 26
        if op in IMMEDIATE and x >> 16 == y >> 16:
            old, new = x & 65535, y & 65535
            if op not in UNSIGNED and max(old,new) >= 32768:
                return []  # signed C expressions need a separate unary/binary expression reader
            evidence['literal', old, new] += 1
        elif op == 0 and (x & 63) in (0,2,3) and not (x >> 21 & 31) and x & ~0x7c0 == y & ~0x7c0:
            evidence['left' if x & 63 == 0 else 'right', x >> 6 & 31, y >> 6 & 31] += 1
        else:
            return []  # don't repair an unrelated constant when registers/control flow changed too
    seen = {}
    for kind,old,new in evidence:
        if seen.setdefault((kind,old),new) != new:
            return []
    return [(*key,count) for key,count in evidence.items()] if len(evidence) <= 2 else []
