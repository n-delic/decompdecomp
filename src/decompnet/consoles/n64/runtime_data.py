"""Find independently called code loaded from an otherwise unowned data tail."""
from collections import Counter, defaultdict
from itertools import combinations

from ...arch.mips import is_frame
from ...arch.mips_references import references
from .loader_code import called_extent


def discover(image, cfg, known, dumps, check=lambda: None):
    """Require two observed copies and a closed caller of two distinct closed callees on one mapping."""
    if len(dumps) < 2:
        return []
    segments = sorted((s for s in cfg.get('segments', []) if isinstance(s, (dict, list))),
                      key=lambda s: s[0] if isinstance(s, list) else s.get('start', -1))
    ranges = []
    for parent, following in zip(segments, segments[1:]):
        if not isinstance(parent, dict) or parent.get('type') != 'code' or not isinstance(parent.get('vram'), int):
            continue
        end = following[0] if isinstance(following, list) else following.get('start')
        subs = sorted((s for s in parent.get('subsegments', []) if isinstance(s, list) and len(s) >= 2),
                      key=lambda s: s[0])
        if not subs or subs[-1][1] not in ('data', '.data', 'rodata', '.rodata') or not isinstance(end, int):
            continue
        lo = subs[-1][0]
        if 0 <= lo < end <= len(image):
            ranges.append((lo, end, parent['vram'] - parent['start']))
    found = defaultdict(dict)
    for lo, hi, old_shift in ranges:
        for at in range((lo + 3) & ~3, hi - 127, 4):
            if not at % 4096:
                check()
            if not is_frame(int.from_bytes(image[at:at + 4], 'big')):
                continue
            head = image[at:at + 128]
            if image.find(head) != at or image.find(head, at + 1) >= 0:
                continue
            locations = Counter()
            for ram in dumps:
                where = ram.find(head)
                if where >= 0 and not where % 4 and ram.find(head, where + 1) < 0:
                    locations[where] += 1
            for where, count in locations.items():
                address = 0x80000000 + where
                shift = address - at
                if count < 2 or shift == old_shift:
                    continue
                size = called_extent(image, at, min(hi, at + 0x10000), address)
                views = {i for i, ram in enumerate(dumps) if ram[where:where + size] == image[at:at + size]} if size else set()
                if len(views) < 2:
                    continue
                # Cutting this tail must not discard a later existing function.
                if any(a < hi and at < b for a, b, _ in known):
                    continue
                if any(v is not None and v < address + size and address < v + b - a for a, b, v in known):
                    continue
                found[shift, lo, hi][(at, at + size, address)] = views
    accepted = []
    for bodies in found.values():
        entries = {v: (a, b, v) for a, b, v in bodies}
        edges = defaultdict(set)
        for a, b, address in bodies:
            for target, _, kind in references(image, a, b, address):
                if kind == 'call' and target in entries and target != address:
                    edges[address].add(target)
        selected = set()
        for caller, targets in edges.items():
            for first, second in combinations(sorted(targets), 2):
                group = (entries[caller], entries[first], entries[second])
                if len(bodies[group[0]] & bodies[group[1]] & bodies[group[2]]) >= 2:
                    selected.update(group)
        for body in sorted(selected):
            # A second location for these bytes or an overlapping candidate cannot establish a unique bank.
            if any(other != body and (other[0] < body[1] and body[0] < other[1]
                   or other[2] < body[2] + body[1] - body[0] and body[2] < other[2] + other[1] - other[0])
                   for group in found.values() for other in group):
                continue
            accepted.append(body)
    return sorted(set(accepted))
