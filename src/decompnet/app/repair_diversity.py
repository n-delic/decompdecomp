"""Prefer measured progress and switch strategy after stalled trials across families."""
import hashlib
import math

from . import region_repairs

LIMIT = 128
STRUCTURAL = ('dead_assignment_narrow_capture', 'narrow_field_update_fold', 'scan_load_capture', 'scan_load_reuse', 'branch_scalar_lifetime',
              'split_integer_lifetime', 'merge_integer_lifetimes', 'paired_integer_lifetimes',
              'block_accumulator_lifetime', 'one_shot_scope', 'structured_backedge',
              'branch_orientation', 'goto_leaf', 'shared_return_tail')


def observe(state, node, output, *, score=None, parent_score=None, rows=None):
    parent = node.get('parent_output')
    path = node.get('path') or []
    if parent is None or output is None or not path:
        return
    parents = state.setdefault('diversity_outputs', {})
    key = hashlib.sha256(repr(parent).encode()).hexdigest()
    row = parents.setdefault(key, dict(streak=0, families=[], yields={}, seen=[], stats={}))
    family = path[-1]
    inert = parent == output
    row['streak'] = row['streak']+1 if inert else 0
    if inert and family not in row['families']:
        row['families'] = (row['families']+[family])[-16:]
    elif not inert:
        row['families'] = []
    comparable=all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 for v in (score,parent_score))
    useful=None
    regional=False
    if comparable:
        before=region_repairs.snapshot(region_repairs.clean(node.get('baseline_rows')))
        after=region_repairs.snapshot(region_repairs.clean(rows))
        regional=bool(region_repairs.gain(before,after)>0 and region_repairs.damage(before,after)==0 and score<=parent_score*1.25)
        useful=not inert and (score<parent_score or regional)
        row['progress_streak']=0 if useful else row.get('progress_streak',0)+1
        families=row.setdefault('progress_families',[])
        if useful:row['progress_families']=[]
        elif family not in families:row['progress_families']=(families+[family])[-16:]
    seen=row.setdefault('seen',[])
    stats=row.setdefault('stats',{}).setdefault(family,dict(trials=0,discoveries=0))
    digest=hashlib.sha256(repr(output).encode()).hexdigest()
    if comparable:
        for label,value in [('judged',1),('useful',int(useful)),('regressed',int(score>parent_score and not regional))]:
            stats[label]=stats.get(label,0)+value
    stats['trials']+=1
    stats['discoveries']+=int(digest!=key and digest not in seen)
    if digest not in seen:seen.append(digest)
    row['seen']=seen[-64:]
    while len(row['stats'])>32:del row['stats'][next(iter(row['stats']))]
    outcomes = row['yields'].setdefault(family, [])
    digest = hashlib.sha256(repr(output).encode()).hexdigest()
    if digest not in outcomes:outcomes.append(digest)
    row['yields'][family] = outcomes[-8:]
    while len(row['yields'])>32:del row['yields'][next(iter(row['yields']))]
    while len(parents)>LIMIT:del parents[next(iter(parents))]
    return useful


def order(state, nodes, slot):
    """Prefer measured useful changes; retain novelty and an untried-family probe every fourth slot."""
    parents = state.get('diversity_outputs', {})
    def priority(node):
        parent = node.get('parent_output')
        family = (node.get('path') or [''])[-1]
        key = hashlib.sha256(repr(parent).encode()).hexdigest()
        row = parents.get(key,{}) if parent is not None else {}
        stalled = plateau(row)
        # A new source shape is a hypothesis; only a compiled output teaches novelty.
        values = row.get('yields',{}).get(family,[])
        stats = row.get('stats',{}).get(family,{})
        novel = stats.get('discoveries',0)/max(1,stats.get('trials',0))>=.25
        if slot%4==3:return (bool(values),0)
        useful=stats.get('useful',0)/max(1,stats.get('judged',0))
        bad=stats.get('judged',0)>=2 and stats.get('regressed',0)*2>=stats['judged']
        tier=0 if useful else 1 if novel and not stats.get('judged') else 2 if not values else 4 if bad else 3
        return (0 if stalled and family in STRUCTURAL else 1,tier,-useful,-int(novel))
    return sorted(nodes,key=priority)


def plateau(row):
    return ((row.get('streak',0)>=6 and len(row.get('families',[]))>=3)
            or (row.get('progress_streak',0)>=6 and len(row.get('progress_families',[]))>=3))


def plateau_count(state):
    return sum(plateau(row)
               for row in state.get('diversity_outputs',{}).values())
