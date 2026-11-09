"""Compose guarded branch/storage/scope hypotheses without adopting intermediate scores.

Every component reparses its parent and retains its own semantic guards. Source is
spliced rather than pretty-printed. The ordinary judge and existing budgets apply.
"""
import re

from . import (
    branch_tail_distribution,
    carrier_sequence_repairs,
    loop_storage_shapes,
    loop_weight_repairs,
    narrow_store_repairs,
    parameter_carriers,
    repair_localisation,
    scalar_storage_reuse,
)

FAMILY = 'record_layout_sequence'
LIMIT = 24
MAX_SOURCE = 65536


def stages(source, name, header, arch, locations=None):
    yield 'tail', branch_tail_distribution.variants(source,name,header,arch)
    yield 'conversion', narrow_store_repairs.variants(source,name,header,arch)
    yield 'storage', (scalar_storage_reuse.variants(source,name,header,arch,locations=locations)
                      + parameter_carriers.variants(source,name,header,arch))
    yield 'loop', loop_storage_shapes.variants(source,name,header,arch,locations=locations)
    yield 'scope', loop_weight_repairs.variants(source,name,header,depths=(1,),focus_lines=repair_localisation.lines(source,locations))


def variants(source, name, header='', arch=None, locations=None):
    if arch not in {'mips','mipsel'} or len(source)+len(header)>MAX_SOURCE:return []
    # Only mixed branch/loop layouts need this dedicated composition lane.
    if not re.search(r'\belse\s*{',source) or not re.search(r'\b(?:do|while|for|goto)\b',source):return []
    # At most two children per component and three distinct components per path.
    # Reserve compositions directly: intermediate regressions need not win the beam.
    pending=[(source,())];seen={source};result=[]
    for _ in range(3):
        following=[]
        for parent,path in pending:
            for kind,choices in stages(parent,name,header,arch,locations):
                if kind in path:continue
                # First/last sites avoid spending both slots on two depths of
                # the same update while omitting the real loop-latch update.
                selected=choices[:1]+choices[-1:] if len(choices)>1 else choices
                for _,text in selected:
                    if text in seen or len(text)+len(header)>MAX_SOURCE:continue
                    seen.add(text);following.append((text,path+(kind,)))
                    if path:result.append((FAMILY,text))
                    if len(result)>=LIMIT:return result
        pending=following[:8]
        if not pending:break
    return result


def applicable(rows, arch):
    return carrier_sequence_repairs.applicable(rows,arch)


def machine_drafts(candidates, name, header, arch):
    """Retain every original; add at most two alternative layouts across all seeds."""
    remaining=2
    for family,source in candidates:
        yield family,source
        if not remaining or len(source)+len(header)>MAX_SOURCE:continue
        choices=(branch_tail_distribution.variants(source,name,header,arch)[:1]
                 +parameter_carriers.variants(source,name,header,arch)[:1])
        for kind,text in choices[:remaining]:
            yield 'm2c_'+kind,text
            remaining-=1
