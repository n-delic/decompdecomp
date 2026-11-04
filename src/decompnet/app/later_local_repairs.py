"""Reuse a dead local's separate later lifetime to avoid unwanted allocator coalescing.

Only straight-line scalar phases are rewritten. CFG liveness, declaration scope and nonescaping
locals establish eligibility; the normal compiler/link judge alone can accept a candidate.
"""
import re

from . import integer_abi, local_flow
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, refs, split_args, view

FAMILY = 'later_local_reuse'
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


def variants(source, name, header='', arch=None):
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
    result = []
    for index,node in sorted(enumerate(flow.nodes),key=lambda item:item[1].start):
        text = part.clean[node.start:node.end]
        hit = re.fullmatch(rf'\s*({IDENT})\s*(\+=|=)\s*({IDENT})\s*\+\s*({IDENT})\s*;\s*',text)
        if not hit or hit[1] not in declarations or {hit[3],hit[4]} & hidden:
            continue
        prefix = part.clean[part.opening+1:node.start].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue  # never insert two statements into an unbraced conditional or loop
        old,operation,left,right = hit.groups()
        operands = [operand_type(part,flow,v,header,arch,name) for v in (left,right)]
        if any(t is None or t[0]>32 for t in operands):
            continue  # do not narrow a wide/pointer expression or change its arithmetic contract
        subtotal_type = declarations[old]['type']
        if integer_abi.scalar(subtotal_type,header+'\n'+source,arch)[1] and (32,False) in operands:
            subtotal_type = 'unsigned int'  # preserve unsigned arithmetic instead of introducing signed overflow
        if operation=='=' and old in {left,right}:
            continue  # compound spelling has an explicit, unambiguous subtotal
        # A compound update reads a real incoming value; prove an ordinary definition dominates it.
        if operation=='+=' and not any(old in flow.nodes[n].defines and n!=index
                                      and flow.nodes[n].start>=declarations[old].end()
                                      for n in dom.get(index,set())):
            continue
        phase = [index]
        while old in set().union(*(flow.nodes[n].live for n in flow.nodes[phase[-1]].successors)):
            current = flow.nodes[phase[-1]]
            if len(current.successors)!=1 or len(phase)>=32:
                break
            following = next(iter(current.successors))
            nxt = flow.nodes[following]
            if following in phase or old in nxt.defines or nxt.start<=current.start:
                break
            phase.append(following)
        else:
            if any(index not in dom.get(n,set()) for n in phase):
                continue  # a bypassing branch must not read an uninitialized donor at the join
            uses = [r for r in refs(part,declarations[old]) if r.start()>=node.end
                    and any(flow.nodes[n].start<=r.start()<flow.nodes[n].end for n in phase[1:])]
            if not uses:
                continue
            stop = max(flow.nodes[n].end for n in phase)
            for later,decl in declarations.items():
                if (later in {old,left,right} or decl.end()>node.start or later in node.live
                        or any(later in flow.nodes[n].uses|flow.nodes[n].defines for n in phase)
                        or integer_abi.scalar(decl['type'],header+'\n'+source,arch)!=
                           integer_abi.scalar(declarations[old]['type'],header+'\n'+source,arch)):
                    continue
                # A separate future value must really exist; liveness proves it is redefined before
                # any later read on every path, including loop backedges and gotos.
                if not any(n.start>=stop and later in n.defines for n in flow.nodes):
                    continue
                if not any(n.start>=stop and later in n.uses for n in flow.nodes):
                    continue
                path = block_path(part,decl.start())
                if any(block_path(part,pos)[:len(path)]!=path for pos in [node.start]+[r.start() for r in uses]):
                    continue
                changes = [(r.start(),r.end(),later) for r in uses]
                if operation=='+=':
                    statement = f'{MARKER} = {left} + {right};\n{later} = {MARKER} + {old};'
                    changes.append((declarations[old].end(),declarations[old].end(),
                                    f'\n{subtotal_type} {MARKER};'))
                else:
                    statement = f'{later} = {left} + {right};'
                changes.append((node.start,node.end,statement))
                candidate = edit(source,changes)
                if (FAMILY,candidate) not in result:
                    result.append((FAMILY,candidate))
                if len(result)>=LIMIT:
                    return result
    return result
