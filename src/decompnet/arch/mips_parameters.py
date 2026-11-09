"""Aligned MIPS evidence for one threshold/subtraction/signed-divisor parameter change."""

import json


def scale_changes(a, b, big):
    def relocations(fn):
        return [row[:3] for row in json.loads(fn['relocs'] or '[]') if len(row) >= 3]

    a, b = dict(a), dict(b)
    left, right = a['target'] or b'', b['target'] or b''
    if not left or len(left) != len(right) or len(left) % 4:
        return []
    order = 'big' if big else 'little'
    xs = [int.from_bytes(left[i:i+4], order) for i in range(0, len(left), 4)]
    ys = [int.from_bytes(right[i:i+4], order) for i in range(0, len(right), 4)]
    relocated = {o // 4 for f in (a, b) for o, _, _ in relocations(f)}
    thresholds = {(x & 65535, y & 65535) for i, (x, y) in enumerate(zip(xs, ys))
                  if i not in relocated and x != y and x >> 16 == y >> 16
                  and x >> 26 == 10 and 1 < (x & 65535) < 32768 and 1 < (y & 65535) < 32768}
    result = []
    for old, new in sorted(thresholds)[:4]:
        allowed, negative, reciprocal = set(), False, False
        shifts = set()
        # Only the positive signed reciprocal form; add/subtract-high forms need another reader.
        for i, (x, y) in enumerate(zip(xs, ys)):
            if x >> 26 != 15 or x >> 16 != y >> 16:
                continue
            reg = x >> 16 & 31
            for j in range(i + 1, min(i + 9, len(xs))):
                u, v = xs[j], ys[j]
                if u >> 26 != 13 or u >> 16 != v >> 16 or u >> 21 & 31 != reg or u >> 16 & 31 != reg:
                    continue
                m, n = ((x & 65535) << 16) | (u & 65535), ((y & 65535) << 16) | (v & 65535)
                old_shifts = [s for s in range(31) if m < 0x80000000 and m == ((1 << (32+s)) + old-1) // old]
                new_shifts = [s for s in range(31) if n < 0x80000000 and n == ((1 << (32+s)) + new-1) // new]
                pairs = [(s, t) for s in old_shifts for t in new_shifts]
                if len(pairs) == 1 and not ({i, j} & relocated):
                    allowed.update((i, j))
                    shifts.update(pairs)
                    reciprocal = True
        for i, (x, y) in enumerate(zip(xs, ys)):
            if x == y or i in relocated or i in allowed:
                continue
            if x >> 16 == y >> 16 and x >> 26 in (9, 10):
                pair = x & 65535, y & 65535
                if x >> 26 == 10 and pair == (old, new):
                    allowed.add(i)
                    continue
                if x >> 26 == 9 and pair == ((-old) & 65535, (-new) & 65535):
                    negative = True
                    allowed.add(i)
                    continue
            if x & ~0x7c0 == y & ~0x7c0 and x >> 26 == 0 and x & 63 == 3:
                if (x >> 6 & 31, y >> 6 & 31) in shifts:
                    allowed.add(i)
                    continue
            # Local absolute jumps move with the function, external jumps do not.
            if x >> 26 == y >> 26 == 2 and a.get('vram') is not None and b.get('vram') is not None:
                xa = ((a['vram'] + i*4 + 4) & 0xf0000000) | ((x & 0x3ffffff) << 2)
                ya = ((b['vram'] + i*4 + 4) & 0xf0000000) | ((y & 0x3ffffff) << 2)
                if 0 <= xa-a['vram'] == ya-b['vram'] < len(left):
                    allowed.add(i)
                    continue
            break
        else:
            if negative and reciprocal:
                result.append((old, new))
    return result

