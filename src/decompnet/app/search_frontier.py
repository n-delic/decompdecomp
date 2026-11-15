"""Small source frontier ranked by instruction structure as well as the ordinary numeric score.

This is search guidance only. Displayed rows never prove byte identity, and alternatives do not
replace the saved source. A normal judge must verify any later improvement.
"""
import hashlib
from collections import Counter
from difflib import SequenceMatcher

from . import allocation_evidence, frame_plateau, permuter_focus, region_repairs

WIDTH = 6
REGIONAL_SLOTS = 2


def source_layout(source):
    """Coarse private search diversity, never evidence of semantic equivalence."""
    # Token counts only rank already judged candidates. Ignore comments/literals.
    from .repair_tokens import TOKEN
    tokens=[m[0] for m in TOKEN.finditer(source) if not m[0].isspace()
            and not m[0].startswith(('/*','//','"',"'"))]
    return tuple(tokens.count(k) for k in ('goto','if','else','do','short','s16','u16'))


def regional_choices(nodes):
    """Reserve different recovered target regions, even when allocation signatures coincide."""
    selected, shapes = [], set()
    for node in sorted(nodes,key=lambda n:(-n.get('region_gain',0),n.get('damage',n.get('region_damage',0)),n['score'])):
        regions = node.get('regions')
        if not regions or node.get('region_gain',0)<=0:
            continue
        shape = tuple(sorted(regions['losses'].items()))
        if shape not in shapes:
            selected.append(node)
            shapes.add(shape)
        if len(selected)==REGIONAL_SLOTS:
            break
    return selected


def mismatch_positions(rows):
    """Target-indexed residuals; advisory only, never a substitute for linked-byte judging."""
    rows = permuter_focus.normalized(rows)
    if any(not isinstance(row,(tuple,list)) or len(row)!=3 or not all(isinstance(v,str) for v in row) for row in rows):
        return set()
    positions = set()
    index = 0
    for marker,target,_ in rows:
        if target:
            if marker != ' ':
                positions.add((index,target))
            index += 1
    return positions


def missing_operations(rows):
    """Target-only non-NOP operations are root-cause leads, not branch displacement noise."""
    rows=permuter_focus.normalized(rows)
    if any(not isinstance(r,(tuple,list)) or len(r)!=3 or not all(isinstance(v,str) for v in r) for r in rows):return 1000000000
    return sum(1 for marker,target,_ in rows
               if marker=='<' and target.strip() and target.split()[0] not in {'nop','b','beq','bne','j'})


def structure(rows):
    rows = permuter_focus.normalized(rows)
    if not rows or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in rows):
        return 1000000000
    return sum(1 if marker in {'r', 's', 'o'} else 3 for marker, _, _ in rows if marker != ' ')


def unhelpful_detour(arch, baseline, rows):
    """Prune expansion, never judging: a register-only parent gained both frame and code size.

    The candidate has already had its ordinary judge. Unknown layouts and improvements in
    instruction structure stay eligible; a later explicit experiment can still try this source.
    """
    if not arch or permuter_focus.classify(baseline) != 'register':
        return False
    from .stack_layout import diagnose
    before = diagnose(arch, baseline)
    after = diagnose(arch, rows)
    if not before or not after or before['frame_delta'] != 0 or after['frame_delta'] is None:
        return False
    target_count = sum(bool(r[1]) for r in rows)
    candidate_count = sum(bool(r[2]) for r in rows)
    return after['frame_delta'] < 0 and candidate_count > target_count and structure(rows) >= structure(baseline)


def structure_recovery(before, after, arch=None):
    """An advisory frontier slot for a recovered full sequence/frame, never a saved winner."""
    a,b = allocation_evidence.diagnose(before,arch),allocation_evidence.diagnose(after,arch)
    return bool(b['register_only'] and a['target_instructions']==b['target_instructions']==b['candidate_instructions']
                and b['target_frame'] is not None and a['target_frame']==b['target_frame']==b['candidate_frame']
                and (not a['register_only'] or a['target_frame']!=a['candidate_frame']))


def allocation_bridge(before, after, arch=None):
    """A scheduled sequence with only register differences earns the next bounded lane."""
    a=allocation_evidence.diagnose(before,arch)
    return bool((a['scheduling'] or a['copy_near_miss']) and a['target_frame'] is not None
                and a['target_frame']==a['candidate_frame'] and structure_recovery(before,after,arch))


def mask_recovery(before, after, arch=None):
    """One advisory sibling for restored halfword masks; no frame growth or large expansion."""
    if arch != 'mips':return False
    from .snapshot_branch_repairs import missing_masks
    old,new=allocation_evidence.diagnose(before,arch),allocation_evidence.diagnose(after,arch)
    return bool(missing_masks(before)>missing_masks(after) and old['target_instructions']
                and old['target_instructions']==new['target_instructions']
                and old['target_frame'] is not None and old['target_frame']==old['candidate_frame']==new['candidate_frame']
                and abs(new['target_instructions']-new['candidate_instructions'])<=2)


def pipeline_recovery(before, after, arch=None):
    """Keep an otherwise recovered operation sequence with at most two extra MIPS NOPs.

    Legacy debug line boundaries can insert a pipeline gap after a successful
    structural change. No instructions are removed from a judge input here.
    """
    if arch!='mips' or not before or not after:return False
    rows=permuter_focus.normalized(after)
    if any(not isinstance(r,(tuple,list)) or len(r)!=3 or not all(isinstance(s,str) for s in r) for r in rows):return False
    def sequence(column):
        return [r[column].split()[0] for r in rows if r[column] and r[column].split()[0]!='nop']
    extra=sum(r[2].split()[0]=='nop' for r in rows if r[2])-sum(r[1].split()[0]=='nop' for r in rows if r[1])
    target,candidate=sequence(1),sequence(2)
    if not target or len(target)>512 or Counter(target)!=Counter(candidate):return False
    # Permit one small pre-existing scheduling region; do not require its repair
    # before preserving a newly recovered arithmetic tail.
    matched=sum(b.size for b in SequenceMatcher(None,target,candidate,autojunk=False).get_matching_blocks())
    if len(target)-matched>2:return False
    a,b=allocation_evidence.diagnose(before,arch),allocation_evidence.diagnose(rows,arch)
    return bool(0<extra<=2
                and a['target_frame'] is not None
                and a['target_frame']==a['candidate_frame']==b['candidate_frame']==b['target_frame']
                and structure(rows)<structure(before))


def shape_axes(rows):
    """Independent register-content/order clues; both still need the normal judge."""
    rows=permuter_focus.normalized(rows)
    if not rows or any(not isinstance(r,(list,tuple)) or len(r)!=3 or not all(isinstance(v,str)for v in r)for r in rows):return None
    target=[r[1] for r in rows if r[1]];candidate=[r[2] for r in rows if r[2]]
    normalized=lambda items:[allocation_evidence.REGISTER.sub('$reg',v)for v in items]
    a,b=normalized(target),normalized(candidate)
    if len(a)>512 or Counter(a)!=Counter(b):return None
    order=len(a)-sum(block.size for block in SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks())
    registers=sum((Counter(target)-Counter(candidate)).values())
    return registers,order


class Frontier:
    def __init__(self):
        self.nodes = []

    def offer(self, source, score, rows, *, arch=None, baseline=None, baseline_score=None):
        """Keep the numeric winner and distinct structural shapes, with bounded source/output counts."""
        if score is None or score <= 0:
            return
        repaired = mismatch_positions(baseline) - mismatch_positions(rows)
        if baseline_score is not None and score>baseline_score*2:
            repaired = set()  # do not reserve the new slot for a large unrelated regression
        if not repaired and (baseline_score is None or score >= baseline_score) and unhelpful_detour(arch, baseline, rows):
            return
        rows = permuter_focus.normalized(rows)
        from ..diff_summary import summarize
        summary = summarize(rows)
        node = {'layout':source_layout(source), 'rows':rows, 'summary': summary, 'source': source, 'score': score, 'fitness': structure(rows),
                'frame_distance': permuter_focus.stack_distance(rows),
                'missing_operations':missing_operations(rows),
                'repaired':tuple(sorted(repaired)), 'kind': permuter_focus.classify(rows), 'regions': region_repairs.snapshot(rows,arch),
                'allocation': allocation_evidence.signature(rows,arch),
                'region_gain': region_repairs.gain(region_repairs.snapshot(baseline,arch),region_repairs.snapshot(rows,arch)),
                'damage': region_repairs.damage(region_repairs.snapshot(baseline,arch), region_repairs.snapshot(rows,arch)),
                'axes':shape_axes(rows),
                'output': hashlib.sha256(repr(rows or source).encode()).hexdigest()}
        pool = [n for n in self.nodes if n['source'] != source]+[node]
        numeric = sorted(pool, key=lambda n: (n['score'], n['fitness'], n['frame_distance']))
        structural = sorted(pool, key=lambda n: (n['damage'], n['fitness'], n['frame_distance'], n['score']))
        # Preserve distinct failure shapes: a moved load can be a useful sibling of a register miss.
        selected = numeric[:1]
        # Preserve one measured alternate tail/storage topology independently of
        # numeric score. It remains private and spends the existing search budget.
        layouts=[n for n in pool if n.get('layout') != selected[0].get('layout')
                 and baseline_score is not None and n['score']<=baseline_score*4]
        if layouts:
            selected.append(min(layouts,key=lambda n:(n['fitness'],n['score'])))
        # Allocation and ordering are separate search lanes. Keep an order recovery
        # even if its numeric score rises; it never replaces the saved winner.
        axes=shape_axes(baseline)
        if axes:
            for axis in (0,1):
                eligible=[n for n in pool if n.get('axes') is not None and n['axes'][axis]<axes[axis]
                          and baseline_score is not None and n['score']<=baseline_score*4]
                if eligible:
                    candidate=min(eligible,key=lambda n:(n['axes'][axis],n['score']))
                    if candidate not in selected:selected.append(candidate)
        # One body-correct root survives even a 2 -> 22 score regression. Width and
        # ordinary depth/compile budgets stay unchanged; this never replaces the saved best.
        frames=[n for n in pool if frame_plateau.gap(n['rows'],arch)]
        if frames:
            candidate=min(frames,key=lambda n:(frame_plateau.gap(n['rows'],arch),n['score']))
            if candidate not in selected:selected.append(candidate)
        masks=[n for n in pool if baseline_score is not None and n['score']<=baseline_score*4
               and mask_recovery(baseline,n['rows'],arch)]
        if masks:
            candidate=min(masks,key=lambda n:(n['missing_operations'],n['fitness'],n['score']))
            if candidate not in selected:selected.append(candidate)
        pipelines=[n for n in pool if baseline_score is not None and n['score']<=baseline_score*4
                   and pipeline_recovery(baseline,n['rows'],arch)]
        if pipelines:
            candidate=min(pipelines,key=lambda n:(n['fitness'],n['score']))
            if candidate not in selected:selected.append(candidate)
        # A slightly worse register-only sibling may be one storage permutation
        # from exact. Reserve it before the other structural slots fill the beam.
        bridges=[n for n in pool if baseline_score is not None and n['score']<=baseline_score*2
                 and allocation_bridge(baseline,n['rows'],arch)]
        if bridges:
            candidate=min(bridges,key=lambda n:n['score'])
            if candidate not in selected:selected.append(candidate)
        # Reserve this before regional/operation slots can consume the whole beam.
        positioned = [n for n in pool if n['summary']['position_differences'] is not None]
        if positioned:
            candidate = min(positioned, key=lambda n:(n['summary']['position_differences'],n['score']))
            if candidate not in selected:selected.append(candidate)
        # Keep a root-operation recovery even if compensating instructions gave a lower score.
        operations = [n for n in pool if baseline_score is not None and n['score']<=baseline_score*2
                      and n['missing_operations']<missing_operations(baseline)]
        if operations:
            candidate=min(operations,key=lambda n:(n['missing_operations'],n['fitness'],n['score']))
            if candidate not in selected:selected.append(candidate)
        # Reserve one sibling that repairs the original target locations even when a new
        # scheduling difference raises its score. Still bounded by WIDTH and ordinary budgets.
        repaired_nodes = sorted((n for n in pool if n.get('repaired')),
                                key=lambda n:(-len(n['repaired']),n['damage'],n['fitness'],n['score']))
        if repaired_nodes and repaired_nodes[0] not in selected:
            selected.append(repaired_nodes[0])
        recovered = [n for n in structural if baseline_score is not None and n['score']<=baseline_score*2
                     and structure_recovery(baseline,rows if n is node else n.get('rows',[]),arch)]
        selected += [n for n in recovered[:1] if n not in selected]
        selected += [n for n in regional_choices(pool) if n not in selected]
        positioned = [n for n in pool if n['summary']['position_differences'] is not None]
        if positioned:
            candidate = min(positioned, key=lambda n:(n['summary']['position_differences'],n['score']))
            if candidate not in selected:
                selected.append(candidate)
        # Reserve distinct failure kinds before filling allocation variants of one kind.
        for candidate in structural:
            if not any(n['kind'] == candidate['kind'] for n in selected):
                selected.append(candidate)
        for candidate in structural:
            if not any(n['allocation'] == candidate['allocation'] for n in selected):
                selected.append(candidate)
        for candidate in structural:
            if candidate not in selected:
                selected.append(candidate)
        self.nodes = []
        counts = {}
        for candidate in selected:
            key = candidate['output']
            if counts.get(key, 0) >= 2:
                continue
            counts[key] = counts.get(key, 0)+1
            self.nodes.append(candidate)
            if len(self.nodes) == WIDTH:
                break

    def choose(self, index, fallback):
        return self.nodes[index % len(self.nodes)]['source'] if self.nodes else fallback
