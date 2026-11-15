"""Advisory target-aligned mismatch regions for retaining useful source alternatives.

Region identity comes from the complete target instruction stream, not candidate row positions.
Inserted instructions cannot slide the comparison. This never supplies a match verdict.
"""
from .permuter_focus import normalized

REGION_INSNS = 16


def snapshot(rows, arch=None):
    targets, losses = [], {}
    positions=[];changed=[]
    for row in normalized(rows):
        if len(row) != 3:
            return None
        marker, target, _ = row
        index = len(targets)
        if target:
            targets.append(target)
        else:
            index = max(0, index-1)
        positions.append(index);changed.append(marker!=' ')
        if marker != ' ':
            region = index // REGION_INSNS
            losses[region] = losses.get(region, 0) + 1
    if arch:
        from ..arch import get
        reader=get(arch).instruction_regions
        graph=reader(targets) if reader else None
        if graph and len(targets)>=2:
            loss={};bad=set()
            for position,mismatch in zip(positions,changed):
                if mismatch:
                    block=str(graph['block_for'][position]);loss[block]=loss.get(block,0)+1;bad.add(position)
            chains={str((a,b,r)):int(a in bad)+int(b in bad) for a,b,r in graph['dependencies']}
            return dict(target=targets,losses=loss,scheme='control-flow',chain_losses=chains,successors=graph['successors'])
    if len(targets) < REGION_INSNS:return None
    return {'target':targets,'losses':{str(k):v for k,v in losses.items()},'scheme':'fixed'}


def gain(before, after):
    """Count recovered mismatches only with the same target stream; total score may still regress."""
    if not before or not after or before['target'] != after['target'] or before.get('scheme','fixed')!=after.get('scheme','fixed'):
        return 0
    block=sum(max(0,old-after['losses'].get(region,0)) for region,old in before['losses'].items())
    chains=sum(max(0,old-after.get('chain_losses',{}).get(edge,old)) for edge,old in before.get('chain_losses',{}).items())
    return block+chains


def damage(before, after):
    """Count new mismatches in previously correct target blocks; advisory, never a veto."""
    if not before or not after or before['target'] != after['target'] or before.get('scheme','fixed')!=after.get('scheme','fixed'):
        return 0
    return (sum(loss for region,loss in after['losses'].items() if not before['losses'].get(region))
            +sum(loss for edge,loss in after.get('chain_losses',{}).items() if not before.get('chain_losses',{}).get(edge)))


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
    parts = [f'First differing target display window: {first//REGION_INSNS+1} (instruction {first+1}; windows of {REGION_INSNS}).']
    if d['register_map']:
        parts.append('Register substitutions: '+', '.join(a+'→'+b for a,b in d['register_map'][:4])+'.')
    if d['scheduling']:
        parts.append('Same instruction multiset: inspect dependency-safe scheduling.')
    elif d['spill_offsets']:
        parts.append('Stack/lifetime pressure differs; try phase splits or disjoint reuse.')
    return ' '.join(parts)+' Advisory only; no source-line or lifetime-conflict proof.'


def focus(rows,evidence=None):
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
    if evidence and evidence.get('available') and evidence.get('removed_count'):
        import hashlib
        targets=[r[1] for r in valid if r[1]]
        if evidence.get('target_sha256')==hashlib.sha256(repr(targets).encode()).hexdigest():
            from collections import Counter
            remaining=Counter(i//REGION_INSNS for i in evidence.get('remaining',[]))
            if remaining:first=remaining.most_common(1)[0][0]
    return [r for r,i in zip(valid,positions) if max(0,first-1)<=i//REGION_INSNS<=first+1]


def order(families, rows, evidence=None):
    """Interleave region-focused and whole-function hypotheses; exclude neither."""
    from itertools import zip_longest

    from . import repair_ranking
    whole = repair_ranking.order(families,rows)
    focused = focus(rows,evidence)
    local = repair_ranking.order(families,focused) if focused else whole
    return list(dict.fromkeys(x for pair in zip_longest(local,whole) for x in pair if x is not None))


def progress(before, after, arch=None, *, reference='explicit parent'):
    """At most three improved/regressed target blocks, never an inferred C line or verdict."""
    left, right = snapshot(clean(before),arch), snapshot(clean(after),arch)
    instruction_note=resolution_text(before,after)
    if not left or not right or left['target'] != right['target'] or left['scheme'] != right['scheme']:
        return instruction_note
    changed = [(block,left['losses'].get(block,0)-right['losses'].get(block,0))
               for block in left['losses'].keys() | right['losses'].keys()]
    improved = sorted((item for item in changed if item[1]>0),key=lambda item:(-item[1],item[0]))[:3]
    regressed = sorted((item for item in changed if item[1]<0),key=lambda item:(item[1],item[0]))[:3]
    if not improved and not regressed:
        return instruction_note
    label = 'blocks' if right['scheme']=='control-flow' else '16-instruction windows'
    parts = []
    for kind, items in [('improved',improved),('regressed',regressed)]:
        if items:parts.append(kind+' '+', '.join(f'{block} ({abs(delta)} differences)' for block,delta in items))
    return 'Target '+label+': '+'; '.join(parts)+f'. Advisory comparison with the {reference}; not C source regions. '+instruction_note


def resolution(before,after):
    """Compare target indices, including insertions; a moved candidate row cannot hide a miss."""
    left,right=clean(before),clean(after)
    def positions(rows):
        targets=[];bad=set()
        for marker,target,_ in rows:
            if target:targets.append(target)
            if marker!=' ':bad.add(max(0,len(targets)-1))
        return targets,bad
    a,old=positions(left);b,new=positions(right)
    if not a or a!=b:return dict(available=False,reason='Complete matching target streams required.')
    import hashlib
    digest=hashlib.sha256(repr(a).encode()).hexdigest()
    return dict(available=True,target_sha256=digest,removed_count=len(old-new),introduced_count=len(new-old),remaining_count=len(new),
                removed=sorted(old-new)[:32],introduced=sorted(new-old)[:32],remaining=sorted(new)[:32],
                note='Zero-based target instruction indices, not C lines; lists limited to 32.')


def resolution_text(before,after):
    result=resolution(before,after)
    if not result['available']:return ''
    if not result['removed_count'] and not result['introduced_count']:return ''
    remaining=', '.join(str(i+1) for i in result['remaining'][:6]) or 'none'
    return (f"Target mismatch sites: {result['removed_count']} removed, {result['introduced_count']} introduced, "
            f"{result['remaining_count']} remaining; next target instructions {remaining} (one-based, not C lines). "
            'Focus subsequent repairs on the surviving differences while preserving the recovered operations.')
