"""Advisory target-aligned mismatch regions for retaining useful source alternatives.

Region identity comes from the complete target instruction stream, not candidate row positions.
Inserted instructions cannot slide the comparison. This never supplies a match verdict.
"""
from .permuter_focus import normalized

REGION_INSNS = 16


def snapshot(rows):
    targets, losses = [], {}
    for row in normalized(rows):
        if len(row) != 3:
            return None
        marker, target, _ = row
        index = len(targets)
        if target:
            targets.append(target)
        else:
            index = max(0, index-1)
        if marker != ' ':
            region = index // REGION_INSNS
            losses[region] = losses.get(region, 0) + 1
    if len(targets) < REGION_INSNS:
        return None
    return {'target': targets, 'losses': {str(k): v for k, v in losses.items()}}


def gain(before, after):
    """Count recovered mismatches only with the same target stream; total score may still regress."""
    if not before or not after or before['target'] != after['target']:
        return 0
    return sum(old - after['losses'].get(region, 0) for region, old in before['losses'].items()
               if old > after['losses'].get(region, 0))


def damage(before, after):
    """Count new mismatches in previously correct target blocks; advisory, never a veto."""
    if not before or not after or before['target'] != after['target']:
        return 0
    return sum(loss for region, loss in after['losses'].items() if not before['losses'].get(region))


def clean(rows):
    """Reject malformed feedback before operand normalization can inspect it."""
    if any(not isinstance(r,(list,tuple)) or len(r)!=3 or not all(isinstance(v,str) for v in r) for r in rows or []):
        return []
    return normalized(rows)


def priority(node):
    """Prefer recovered target blocks without damaging previously correct ones."""
    return (node.get('region_damage', 0), -node.get('region_gain', 0), node['score'])


def summary(rows, arch=None):
    """A compact instruction-index report, not an inferred C-to-assembly mapping."""
    from .allocation_evidence import diagnose
    valid = clean(rows)
    target_index = 0
    first = None
    for marker, target, _ in valid:
        if marker != ' ' and first is None:
            first = target_index
        target_index += bool(target)
    if first is None:
        return ''
    d = diagnose(valid, arch)
    parts = [f'First differing target block: {first//REGION_INSNS+1} (instruction {first+1}; blocks of {REGION_INSNS}).']
    if d['register_map']:
        parts.append('Register substitutions: '+', '.join(a+'→'+b for a,b in d['register_map'][:4])+'.')
    if d['scheduling']:
        parts.append('Same instruction multiset: inspect dependency-safe scheduling.')
    elif d['spill_offsets']:
        parts.append('Stack/lifetime pressure differs; try phase splits or disjoint reuse.')
    return ' '.join(parts)+' Advisory only; no source-line or lifetime-conflict proof.'


def focus(rows):
    """First differing target block plus adjacent context for family ranking only."""
    valid = clean(rows)
    positions = []; index = 0; first = None
    for row in valid:
        positions.append(index)
        if row[0]!=' ' and first is None:
            first = index//REGION_INSNS
        index += bool(row[1])
    if first is None:
        return []
    return [r for r,i in zip(valid,positions) if max(0,first-1)<=i//REGION_INSNS<=first+1]


def order(families, rows):
    """Interleave region-focused and whole-function hypotheses; exclude neither."""
    from itertools import zip_longest

    from . import repair_ranking
    whole = repair_ranking.order(families,rows)
    focused = focus(rows)
    local = repair_ranking.order(families,focused) if focused else whole
    return list(dict.fromkeys(x for pair in zip_longest(local,whole) for x in pair if x is not None))
