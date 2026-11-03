"""Small source frontier ranked by instruction structure as well as the ordinary numeric score.

This is search guidance only. Displayed rows never prove byte identity, and alternatives do not
replace the saved source. A normal judge must verify any later improvement.
"""
import hashlib

from . import allocation_evidence, permuter_focus, region_repairs

WIDTH = 6
REGIONAL_SLOTS = 2


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


class Frontier:
    def __init__(self):
        self.nodes = []

    def offer(self, source, score, rows, *, arch=None, baseline=None, baseline_score=None):
        """Keep the numeric winner and distinct structural shapes, with bounded source/output counts."""
        if score is None or score <= 0:
            return
        if (baseline_score is None or score >= baseline_score) and unhelpful_detour(arch, baseline, rows):
            return
        rows = permuter_focus.normalized(rows)
        from ..diff_summary import summarize
        summary = summarize(rows)
        node = {'summary': summary, 'source': source, 'score': score, 'fitness': structure(rows),
                'frame_distance': permuter_focus.stack_distance(rows),
                'kind': permuter_focus.classify(rows), 'regions': region_repairs.snapshot(rows,arch),
                'allocation': allocation_evidence.signature(rows,arch),
                'region_gain': region_repairs.gain(region_repairs.snapshot(baseline,arch),region_repairs.snapshot(rows,arch)),
                'damage': region_repairs.damage(region_repairs.snapshot(baseline,arch), region_repairs.snapshot(rows,arch)),
                'output': hashlib.sha256(repr(rows or source).encode()).hexdigest()}
        pool = [n for n in self.nodes if n['source'] != source]+[node]
        numeric = sorted(pool, key=lambda n: (n['score'], n['fitness'], n['frame_distance']))
        structural = sorted(pool, key=lambda n: (n['damage'], n['fitness'], n['frame_distance'], n['score']))
        # Preserve distinct failure shapes: a moved load can be a useful sibling of a register miss.
        selected = numeric[:1]
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
