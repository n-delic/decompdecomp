"""Measured frame and pointer-tail proposals; all share ordinary candidate/time budgets."""
from .. import arch as arches
from . import branch_tail_distribution, stack_padding_repairs

FAMILIES=('inferred_frame_reservation', 'branch_packing_tail', 'frame_branch_tail_sequence')


def variants(source,name,header,arch,rows):
    reader=arches.get(arch).stack_layout
    evidence=reader(rows) if reader else {}
    frames=stack_padding_repairs.measured(source,name,arch,rows)
    tails=branch_tail_distribution.variants(source,name,header,arch) if evidence.get('pointer_swap') else []
    result=list(frames)
    # Keep the independently judged frame and tail controls before the composition.
    if frames:
        for _,framed in frames[:1]:
            result.extend(('frame_branch_tail_sequence',s) for _,s in branch_tail_distribution.variants(framed,name,header,arch)[:2])
    result.extend(tails[:2])
    return list(dict.fromkeys(result))

