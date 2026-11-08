"""Shared target-bound binary identities recorded by console SDK scanners.

A past detection cannot classify a changed target or override a disabled corpus.
The metadata holds no code and never establishes a verified C match.
"""

import json
from collections import Counter
from pathlib import Path


def current(proj, hint):
    if not hint.get('binary_evidence'):
        return True
    try:
        root = Path(proj['root']).resolve()
        target = (root / hint['target_path']).resolve()
        target.relative_to(root)
        stat = target.stat()
        return [stat.st_mtime_ns, stat.st_size] == hint['target_stamp']
    except (OSError, KeyError, ValueError):
        return False


def identities(net, proj, *, include_asm=False, selected=True):
    """Use complete current targets; leave ambiguous names and reference-only leads for review."""
    selection = net.cfg(proj).get('sdk_libraries')
    if selected and selection == []:
        return {}
    rows = net.project_store(proj['id']).all("SELECT id,size,meta FROM functions WHERE project_id=? AND "
                                            "json_extract(meta,'$.sdk_identity') IS NOT NULL", proj['id'])
    if not rows:
        return {}
    prints = net.twins.prints(proj)
    multiplicity = Counter(p.key for p in prints.values())
    out = {}
    for row in rows:
        hint = json.loads(row['meta'])['sdk_identity']
        target = prints.get(row['id'])
        if (target is None or multiplicity[target.key] != 1 or target.key != hint.get('fingerprint') or target.size != hint.get('size')
                or row['size'] != target.size or (target.asm and not include_asm) or len(hint.get('names', [])) != 1
                or (selected and selection is not None and not set(hint['corpora']) & set(selection))):
            continue
        if current(proj, hint):
            out[row['id']] = hint
    return out


def brief(net, proj, fn):
    """Keep the same evidence and uncertainty visible to agents as to the function drawer."""
    hint = net.sdk_functions(proj).get(fn['id'])
    if not hint:
        return []
    lines = ['', 'SDK identity: '+', '.join(hint['names'])+' ('+'; '.join(hint['corpora'])+').',
             'This identifies a binary pattern, not matching C, C types or an exact SDK release.']
    for item in hint.get('binary_evidence', []):
        lines.append(f"Direct pattern {item['hash']} from {item['source']}; target SHA-256 {item['target_sha256']}.")
        for ref in item.get('references', [])[:8]:
            lines.append(f"Reference lead only: {ref['target']} may be {ref['name']}; verify it independently.")
    return lines
