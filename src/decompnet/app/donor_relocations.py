"""Find full-size donor candidates when ELF and ROM relocation inference differs."""

import json
from collections import defaultdict

from ..elfobj import Reloc, mask_relocs
from .cross_game_progress import report
from .twins import ACROSS_MIN_BYTES, Print, _key


def _layout(row):
    return sorted((entry[0], entry[1], entry[2]) for entry in json.loads(row['relocs'] or '[]') if len(entry) >= 3)


def candidates(net, proj, donors, job):
    """Mask the ROM bytes with the donor's own relocation positions before comparing."""
    by_size = defaultdict(dict)
    for donor in donors:
        if donor.size >= ACROSS_MIN_BYTES and donor.layout is not None and not donor.asm:
            layout = tuple(map(tuple, donor.layout))
            by_size[donor.size].setdefault(layout, {})[donor.key] = donor.prefix
    if not by_size:
        return {}
    # Use only bits of the first eight bytes that survive *every* donor
    # layout of this size. Most destination functions can skip every full
    # mask and hash. The complete fingerprint still decides each hit.
    processor, big = proj['arch'], bool(proj['big_endian'])
    indexed = {}
    for size, layouts in by_size.items():
        keep = (1 << 64) - 1
        prepared = {}
        for layout, keys in layouts.items():
            if job.stop.is_set():
                return {}
            relocs = [Reloc(o, t, '', 0) for o, t in layout]
            keep &= int.from_bytes(mask_relocs(b'\xff' * size, relocs, processor, big)[:8], 'big')
            prepared[layout] = relocs, list(layout), keys
        prefixes = defaultdict(set)
        unknown = set()
        for layout, (_, _, keys) in prepared.items():
            for prefix in keys.values():
                if prefix:
                    prefixes[int(prefix, 16) & keep].add(layout)
                else:
                    unknown.add(layout)
        indexed[size] = keep, prefixes, unknown, prepared

    report(job, 'Comparing code with donor relocation layouts')
    matches = defaultdict(list)
    sizes = json.dumps(list(indexed))
    for row in net.project_store(proj['id']).all(
        "SELECT id,size,target,relocs FROM functions WHERE project_id=? AND status='open' "
        "AND size IN (SELECT value FROM json_each(?))", proj['id'], sizes,
    ):
        if job.stop.is_set():
            return {}
        code = row['target']
        if not code or len(code) != row['size']:
            continue
        keep, prefixes, unknown, prepared = indexed[row['size']]
        bucket = prefixes.get(int.from_bytes(code[:8], 'big') & keep)
        if not bucket and not unknown:
            continue
        layouts = (bucket | unknown) if bucket else unknown
        original = [(offset, kind) for offset, kind, _ in _layout(row)]
        for layout in layouts:
            relocs, offsets, keys = prepared[layout]
            if offsets == original:
                continue  # the ordinary fingerprint pass already covers this
            key = _key(mask_relocs(code, relocs, processor, big), offsets)
            if key in keys:
                matches[key].append(row['id'])
    return matches


def donor_refs(donor, row, label):
    """Align known ROM addresses by instruction position. Leave unresolved names for the judge."""
    actual = {(offset, kind): address for offset, kind, address in _layout(row)}
    refs = [label(actual[tuple(pair)]) if tuple(pair) in actual else ref
            for pair, ref in zip(donor.layout, donor.refs, strict=True)]
    return Print(donor.key, refs, donor.size)
