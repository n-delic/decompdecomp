"""Small measured loop combinations inside the existing phase budget, never match verdicts."""
from . import allocation_evidence, region_repairs, search_frontier

FAMILIES=('explicit_loop_backedge','row_number_staging','branch_integer_constant')
MAX_PROPOSALS=6
MAX_PARENTS=3
PER_PARENT=2
MAX_EXTRA_INSTRUCTIONS=4


def eligible(node, report, arch=None):
    path=node.get('path') or []
    score=report.get('score')
    if (not path or path[-1] not in FAMILIES or not isinstance(score,(int,float))
            or not 0<score<=node['ceiling']*4 or node.get('depth',0)>=3):return False
    before=allocation_evidence.diagnose(node.get('baseline_rows'),arch)
    after=allocation_evidence.diagnose(report.get('rows'),arch)
    if (not after['target_instructions'] or before['target_frame'] is None
            or before['target_frame']!=before['candidate_frame'] or before['target_frame']!=after['candidate_frame']
            or abs(after['candidate_instructions']-after['target_instructions'])>MAX_EXTRA_INSTRUCTIONS):return False
    repaired=search_frontier.mismatch_positions(node.get('baseline_rows'))-search_frontier.mismatch_positions(report.get('rows'))
    # A real backedge can expose the counter-staging prerequisite before any
    # target position improves. Its speculative children still consume the same
    # six-proposal cap; unknown frames and larger regressions remain ineligible.
    prerequisite=path[-1]=='explicit_loop_backedge' or path[-2:]==['explicit_loop_backedge','row_number_staging']
    return bool(prerequisite or repaired or score<node.get('parent_score',0) or
                region_repairs.gain(node.get('region_baseline'),region_repairs.snapshot(report.get('rows'),arch)))


def offer(state,parent,variants,arch=None):
    remaining=MAX_PROPOSALS-state.get('loop_proposals',0)
    parents=state.setdefault('loop_parents',[])
    if remaining<=0 or len(parents)>=MAX_PARENTS or parent['source'] in parents:return 0
    path=parent.get('path',[])
    preferred={'explicit_loop_backedge':('row_number_staging','branch_integer_constant'),
               'row_number_staging':('branch_integer_constant',),
               'branch_integer_constant':('row_number_staging',)}.get(path[-1],())
    proposals=variants(parent['source'],{**parent['report'],'repair_path':path})
    choices=[p for p in proposals if isinstance(p,tuple) and p[0] in preferred and p[1] not in state['seen']]
    choices=sorted(choices,key=lambda p:preferred.index(p[0]))[:min(remaining,PER_PARENT)]
    if not choices:return 0
    children=[]
    from .iterative_repairs import output_key
    for family,source in choices:
        if source in state['seen']:continue
        state['seen'].append(source)
        children.append(dict(source=source,depth=parent['depth']+1,ceiling=parent['ceiling'],
            path=path+[family],parent_source=parent['source'],parent_output=output_key(parent['report']),
            parent_score=parent['score'],baseline_rows=parent['report'].get('rows'),
            region_baseline=parent.get('regions'),loop_probe=True))
    parents.append(parent['source']);state['loop_proposals']=state.get('loop_proposals',0)+len(children)
    state['pending']=children+state['pending']
    return len(children)
