"""A small earned continuation for measured complementary repairs, not a wider global search."""
from . import allocation_evidence, repair_selection

EXTRA_DEPTH = 2
MAX_PROPOSALS = 4
MAX_PARENTS = 2
PER_PARENT = 2
FAMILIES = {'narrow_call_destination','typed_pointer_snapshot','branch_scalar_lifetime',
            'inline_call_count','embedded_call_assignment'}


def eligible(parent, depth, arch=None):
    """Require changed machine code, repeated measured progress and preserved frame/instruction counts."""
    path=parent.get('path',[])
    if (not depth<=parent.get('depth',0)<depth+EXTRA_DEPTH or len(set(path)&FAMILIES)<3
            or parent.get('productive_steps',0)<2 or parent.get('unchanged_steps',0)
            or parent.get('output') is None or parent.get('parent_output') is None
            or parent['output']==parent['parent_output']
            or not 0<parent.get('score',0)<parent.get('ceiling',0)):
        return False
    before=allocation_evidence.diagnose(parent.get('baseline_rows'),arch)
    after=allocation_evidence.diagnose(parent.get('report',{}).get('rows'),arch)
    return bool(after['target_instructions'] and before['target_instructions']==after['target_instructions']
                and after['target_instructions']==after['candidate_instructions']
                and before['target_frame']==before['candidate_frame']==after['candidate_frame'])


def select(state, parent, proposals):
    """At most four ordinary-budget proposals, from two already-judged parents; no raw edit splicing."""
    remaining=MAX_PROPOSALS-state.get('sequence_proposals',0)
    parents=state.setdefault('sequence_parents',[])
    if remaining<=0 or len(parents)>=MAX_PARENTS or parent['source'] in parents:return []
    preferred=repair_selection.COMPLEMENTS.get(parent['path'][-1],())
    choices=[p for p in proposals if isinstance(p,tuple) and p[0] in FAMILIES
             and p[0] in preferred and p[0] not in parent['path'] and p[1] not in state['seen']]
    selected=sorted(choices,key=lambda p:preferred.index(p[0]))[:min(PER_PARENT,remaining)]
    if selected:
        parents.append(parent['source']);state['sequence_proposals']=state.get('sequence_proposals',0)+len(selected)
    return selected
