"""Change approach after measured inert output across families, without predicting uncompiled C."""
import hashlib

LIMIT = 128
STRUCTURAL = ('scan_load_capture', 'scan_load_reuse', 'branch_scalar_lifetime',
              'split_integer_lifetime', 'merge_integer_lifetimes', 'paired_integer_lifetimes',
              'block_accumulator_lifetime', 'one_shot_scope', 'structured_backedge',
              'branch_orientation', 'goto_leaf', 'shared_return_tail')


def observe(state, node, output):
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
    seen=row.setdefault('seen',[])
    stats=row.setdefault('stats',{}).setdefault(family,dict(trials=0,discoveries=0))
    digest=hashlib.sha256(repr(output).encode()).hexdigest()
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


def order(state, nodes, slot):
    """Use observed novelty, not scores, and reserve every fourth slot for an untried family."""
    parents = state.get('diversity_outputs', {})
    def priority(node):
        parent = node.get('parent_output')
        family = (node.get('path') or [''])[-1]
        key = hashlib.sha256(repr(parent).encode()).hexdigest()
        row = parents.get(key,{}) if parent is not None else {}
        plateau = row.get('streak',0)>=6 and len(row.get('families',[]))>=3
        # A new source shape is a hypothesis; only a compiled output teaches novelty.
        values = row.get('yields',{}).get(family,[])
        stats = row.get('stats',{}).get(family,{})
        novel = stats.get('discoveries',0)/max(1,stats.get('trials',0))>=.25
        if slot%4==3:return (bool(values),0)
        return (0 if plateau and family in STRUCTURAL else 1,
                0 if novel else 1 if not values else 2)
    return sorted(nodes,key=priority)


def plateau_count(state):
    return sum(row['streak']>=6 and len(row['families'])>=3
               for row in state.get('diversity_outputs',{}).values())
