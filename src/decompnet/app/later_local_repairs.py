"""Reuse a dead local's separate later lifetime to avoid unwanted allocator coalescing.

Only bounded, dominated scalar live regions are rewritten. CFG liveness, declaration scope and nonescaping
locals establish eligibility; the normal compiler/link judge alone can accept a candidate.
"""
import re

from . import integer_abi, later_local_operations, later_local_phases, lifetime_donor_ranking, local_flow
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, refs, split_args, view

FAMILY = 'later_local_reuse'
FAMILIES = (FAMILY,'later_operation_reuse','later_call_reuse','branch_local_reuse')
LIMIT = 20
MARKER = '__repair_subtotal'


def operand_type(part, flow, variable, header, arch, name):
    """Only ordinary scalar locals, parameters and explicit extern scalar declarations."""
    context = header+'\n'+part.source
    for declaration in flow.locals:
        if declaration['name']==variable:
            return integer_abi.scalar(declaration['type'],context,arch)
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^{}]*)\)\s*$',part.clean[:part.opening])
    if signature:
        for a,b in split_args(signature[1]):
            parameter = re.fullmatch(r'\s*(.+?)\s+'+re.escape(variable)+r'\s*',signature[1][a:b])
            if parameter:
                return integer_abi.scalar(parameter[1],context,arch)
    declarations = re.findall(r'\bextern\s+([^;{}]+?)\s+'+re.escape(variable)+r'\s*;',context)
    types = [integer_abi.scalar(t,context,arch) for t in declarations]
    return types[0] if types and types[0] is not None and all(t==types[0] for t in types) else None


def variants(source, name, header='', arch=None, evidence=None):
    part = view(source,name)
    if not part or MARKER in source+header:
        return []
    # A volatile return qualifier in a separate prototype is not an escaped local. Do not
    # disable this family for it; reject control extensions and qualified accesses in this body.
    if re.search(r'\b(?:volatile|asm|__asm__|setjmp|_setjmp|sigsetjmp|longjmp)\b',part.body):
        return []
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+source))
    if macros & set(re.findall(IDENT,part.body)):
        return []  # unknown macro reads/writes, including nested captures, are not liveness proof
    try:
        flow = local_flow.Flow(part)
    except (ValueError,RecursionError):
        return []
    declarations = {d['name']:d for d in flow.locals
                    if integer_abi.scalar(d['type'],header+'\n'+source,arch) in {(32,True),(32,False)}}
    hidden = macros
    # Notice aliases/pointer/qualified shadows too, not just the supported scalar spellings.
    for variable in list(declarations):
        shadows = [m for m in re.finditer(rf'\b({IDENT})(?:\s+\**\s*|\s*\*+\s*){variable}\s*(?=[;=,\[])',part.body)
                   if m[1] not in {'return','goto','case','break','continue','else','do','sizeof'}]
        if len(shadows)!=1:
            declarations.pop(variable)
    declarations = {k:v for k,v in declarations.items() if k not in hidden}
    dom = dominators(flow)
    pools = {family:[] for family in FAMILIES}
    for index,node in sorted(enumerate(flow.nodes),key=lambda item:item[1].start):
        text = part.clean[node.start:node.end]
        def resolve(value, spelling=False):
            return integer_abi.scalar(value,header+'\n'+source,arch) if spelling else operand_type(part,flow,value,header,arch,name)
        assignment = later_local_operations.parse(text,header+'\n'+source,resolve)
        if assignment is None or assignment.name not in declarations:
            continue
        old = assignment.name
        old_type=integer_abi.scalar(declarations[old]['type'],header+'\n'+source,arch)
        subtotal_type=later_local_operations.subtotal_type(assignment,old_type,resolve,declarations[old]['type'])
        if subtotal_type is None:continue
        prefix = part.clean[part.opening+1:node.start].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue  # never insert two statements into an unbraced conditional or loop
        if (assignment.compound!='=' or old in node.uses) and not any(
                old in flow.nodes[n].defines and n!=index and flow.nodes[n].start>=declarations[old].end()
                for n in dom.get(index,set())):
            continue
        phase=later_local_phases.region(flow,index,old,dom)
        if not phase:continue
        uses = [r for r in refs(part,declarations[old]) if r.start()>=node.end
                and any(flow.nodes[n].start<=r.start()<flow.nodes[n].end for n in phase if n!=index)]
        if not uses:continue
        stop=max(flow.nodes[n].end for n in phase)
        donors=lifetime_donor_ranking.rank(flow,phase,list(declarations),evidence,source,header)
        branch=any(len(flow.nodes[n].successors)>1 for n in phase)
        family=('branch_local_reuse' if branch else 'later_call_reuse' if assignment.operator=='call'
                else FAMILY if assignment.operator=='+' else 'later_operation_reuse')
        for later in donors:
            decl=declarations[later]
            if (later in {old,assignment.left,assignment.right} or decl.end()>node.start or later in node.live
                    or any(later in flow.nodes[n].uses|flow.nodes[n].defines for n in phase)
                    or integer_abi.scalar(decl['type'],header+'\n'+source,arch)!=old_type):
                continue
            if not any(n.start>=stop and later in n.defines for n in flow.nodes):continue
            if not any(n.start>=stop and later in n.uses for n in flow.nodes):continue
            path=block_path(part,decl.start())
            if any(block_path(part,pos)[:len(path)]!=path for pos in [node.start]+[r.start() for r in uses]):continue
            changes=[(r.start(),r.end(),later) for r in uses]
            if assignment.compound!='=':
                operator=assignment.compound[:-1]
                expression=(f'{MARKER} + {old}' if operator=='+' else f'{old} {operator} {MARKER}')
                statement=f'{MARKER} = {assignment.expression};\n{later} = {expression};'
                changes.append((declarations[old].end(),declarations[old].end(),f'\n{subtotal_type} {MARKER};'))
            else:
                statement=f'{later} = {assignment.expression};'
            changes.append((node.start,node.end,statement))
            candidate=edit(source,changes)
            if (family,candidate) not in pools[family] and len(pools[family])<LIMIT:
                pools[family].append((family,candidate))
    # Preserve the measured short addition lane, then rotate the extended families fairly.
    from itertools import zip_longest
    first=pools[FAMILY][:4];pools[FAMILY]=pools[FAMILY][4:]
    return (first+[item for row in zip_longest(*pools.values()) for item in row if item])[:LIMIT]
