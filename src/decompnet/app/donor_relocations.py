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
            by_size[donor.size].setdefault(tuple(map(tuple, donor.layout)), set()).add(donor.key)
    if not by_size:
        return {}
    report(job, 'Comparing code with donor relocation layouts')
    matches = defaultdict(list)
    for row in net.project_store(proj['id']).all(
        "SELECT id,size,target,relocs FROM functions WHERE project_id=? AND status='open' AND size>=?",
        proj['id'], ACROSS_MIN_BYTES,
    ):
        if job.stop.is_set():
            return {}
        code = row['target']
        if not code or len(code) != row['size']:
            continue
        original = [(offset, kind) for offset, kind, _ in _layout(row)]
        for donor_layout, keys in by_size.get(len(code), {}).items():
            if list(donor_layout) == original:
                continue  # the ordinary fingerprint pass already covers this
            masked = mask_relocs(code, [Reloc(o, t, '', 0) for o, t in donor_layout],
                                 proj['arch'], bool(proj['big_endian']))
            key = _key(masked, list(donor_layout))
            if key in keys:
                matches[key].append(row['id'])
    return matches


def donor_refs(donor, row, label):
    """Align known ROM addresses by instruction position. Leave unresolved names for the judge."""
    actual = {(offset, kind): address for offset, kind, address in _layout(row)}
    refs = [label(actual[tuple(pair)]) if tuple(pair) in actual else ref
            for pair, ref in zip(donor.layout, donor.refs, strict=True)]
    return Print(donor.key, refs, donor.size)
