"""Bounded compositions of existing scope, narrow-load and storage-carrier repairs.

Each step uses the original repair's guards. No statement shuffle, guessed types,
ABI change or compiler override is introduced. The normal judge decides acceptance.
"""
from decomprepair import compiler_shape_repairs

from . import allocation_evidence, narrow_load_subtraction, parameter_carriers

FAMILY = 'narrow_carrier_sequence'
LIMIT = 32
MAX_SOURCE = 80000


def variants(source, name, header='', arch=None):
    if len(source)+len(header)>MAX_SOURCE:return []
    roots=[source]+[text for family,text in compiler_shape_repairs.variants(source,name,arch,header=header)
                    if family=='one_shot_scope'][:2]
    result=[]
    for root in roots:
        # Re-parse every intermediate; never splice edits from different parents.
        for _,narrow in narrow_load_subtraction.variants(root,name,header,arch)[:2]:
            for _,candidate in parameter_carriers.variants(narrow,name,header,arch):
                if candidate!=source:result.append((FAMILY,candidate))
                if len(result)>=LIMIT:return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))


def applicable(rows, arch):
    d=allocation_evidence.diagnose(rows,arch)
    return bool(d['target_instructions'] and d['target_instructions']<=512
                and d['target_instructions']==d['candidate_instructions']
                and d['target_frame'] is not None and d['target_frame']==d['candidate_frame']
                and d['differences']<=max(4,d['target_instructions']//4)
                and (d['register_only'] or d['scheduling'] or d['allocation_schedule']))


def machine_drafts(candidates, name, header, arch):
    """Keep original drafts first; at most three guarded lifetime/load alternatives total.

    No second decompiler run and no shared context edits. More involved compositions
    remain in the measured near-match search instead of inflating initial drafting.
    """
    from .record_layout_search import machine_drafts as layout_drafts
    candidates=layout_drafts(candidates,name,header,arch)
    remaining=3
    for family,source in candidates:
        yield family,source
        if not remaining or len(source)+len(header)>MAX_SOURCE:continue
        choices=(narrow_load_subtraction.variants(source,name,header,arch)[:1]
                 +parameter_carriers.variants(source,name,header,arch)[:1])
        for kind,candidate in list(dict.fromkeys(choices))[:remaining]:
            yield 'm2c_'+kind,candidate
            remaining-=1
