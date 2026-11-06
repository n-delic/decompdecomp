"""Reuse existing, unescaped record fields without guessing types or changing shared headers.

Only straight-line scalar lifetimes are forwarded. CFG liveness proves the old value is dead
at the boundary; address taking, calls, macros and unknown field types prevent a proposal.
"""
import re

from ..forks.csource import _blank
from . import local_flow
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, locals_for, refs, view
from .pattern_repairs import close

FAMILIES = ('record_value_forwarding', 'narrow_definition_lifetime', 'sink_zero_initialization')


def fields(part, header):
    context = _blank(header)+'\n'+part.clean
    types = {}
    for hit in re.finditer(rf'\btypedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*({IDENT})\s*;', context):
        if hit[2] not in types:
            types[hit[2]] = hit[1]
        else:
            types[hit[2]] = ''  # conflicting declarations are not evidence
    result = {}
    declarations = [(m[2], m[1]) for m in re.finditer(rf'\bstruct\s*{{([^{{}}]*)}}\s*({IDENT})\s*;', part.body)]
    declarations += [(m[2], types[m[1]]) for m in re.finditer(rf'\b({IDENT})\s+({IDENT})\s*;', part.body) if m[1] in types]
    for variable, body in declarations:
        if re.search(r'\b(?:volatile|union)\b', body):
            continue
        if sum(v == variable for v, _ in declarations) != 1:
            continue
        members = {}
        for decl in re.finditer(rf'(?:^|;)\s*({INTEGER})\s+({IDENT}(?:\s*,\s*{IDENT})*)\s*;', body):
            for member in re.findall(IDENT, decl[2]):
                members[member] = ' '.join(decl[1].split())
        result[variable] = members
    return result


def straight_nodes(flow, node, limit=12):
    """Follow one basic block, stopping before control flow or another entry."""
    predecessors = {i: set() for i in range(len(flow.nodes))}
    for i, item in enumerate(flow.nodes):
        for nxt in item.successors:
            predecessors[nxt].add(i)
    result = [node]
    for _ in range(limit):
        current = result[-1]
        if len(current.successors) != 1:
            break
        nxt = next(iter(current.successors))
        item = flow.nodes[nxt]
        text = flow.part.clean[item.start:item.end]
        if (item in result or len(predecessors[nxt]) != 1 or item.start < current.end
                or not re.fullmatch(r'\s*[^;{}]+;\s*', text)
                or re.match(r'\s*(?:return|goto|break|continue|if|while|for|switch)\b', text)):
            break
        result.append(item)
    return result


def variants(source, name, header=''):
    part = view(source, name)
    flow = local_flow.analyze(source, name)
    if not part or not flow:
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')', source+'\n'+header, re.M))
    records = fields(part, header)
    result = []
    for decl in locals_for(part):
        variable = decl['name']
        for hit in refs(part, decl):
            node = flow.at(hit.start())
            if not node:
                continue
            definition = re.fullmatch(rf'\s*{variable}\s*=(?!=)([^;{{}}]+);\s*', part.clean[node.start:node.end])
            if not definition or re.search(rf'\b{variable}\b', definition[1]):
                continue
            chain = straight_nodes(flow, node)
            # One explicitly narrowed definition can use a narrow local until the next kill.
            cast = re.match(r'\s*\(\s*(s16|u16|short|unsigned short)\s*\)', definition[1])
            if cast:
                operand=definition[1][cast.end():].strip()
                # A cast of the left operand does not narrow the whole expression:
                # (short)a - (short)b can be outside the signed-halfword range.
                whole=(close(operand,0)==len(operand) if operand.startswith('(')
                       else re.fullmatch(rf'{IDENT}|-?{NUMBER}',operand) is not None)
                if not whole:
                    cast=None
            if cast and decl['type'] in {'s32', 'int', 'u32', 'unsigned int'}:
                compatible = decl['type'] in {'s32', 'int'}
                end = next((n.start for n in chain[1:] if variable in n.defines), chain[-1].end)
                segment = part.clean[node.start:end]
                # The last node's successors must kill or not read the old value on every path.
                boundary = flow.at(end)
                dead = variable not in boundary.live if boundary else all(variable not in flow.nodes[i].live for i in chain[-1].successors)
                uses = [u for u in refs(part, decl) if node.start <= u.start() < end]
                fresh = '__repair_narrow_'+variable
                if (compatible and dead and len(uses)>1 and fresh not in source and not macros.intersection(re.findall(IDENT,segment))
                        and not re.search(r'\b(?:sizeof|typeof|__typeof__)\b',segment)
                        and len(re.findall(rf'\b{variable}\s*=(?!=)',segment))==1
                        and not re.search(r'\+\+|--|\b'+variable+r'\s*(?:[+*/%&|^<>-]=)',segment)):
                    result.append((FAMILIES[1], edit(source,[(decl.end(),decl.end(),f'\n{cast[1]} {fresh};')]+
                                                       [(u.start(),u.end(),fresh) for u in uses])))
            # Do not move evaluations across calls or control flow. A cast is not a call.
            window = []
            for item in chain:
                text = part.clean[item.start:item.end]
                if (re.search(r'\b'+IDENT+r'\s*\(|\+\+|--', text)
                        or macros.intersection(re.findall(IDENT,text))):
                    break
                if item is not node and variable in item.defines:
                    break
                window.append(item)
            if len(window)<2:
                continue
            for copy in window[1:]:
                store = re.fullmatch(rf'\s*({IDENT})\s*\.\s*({IDENT})\s*=\s*{variable}\s*;\s*',part.clean[copy.start:copy.end])
                if not store or records.get(store[1],{}).get(store[2]) != decl['type']:
                    continue
                # A later &record at the consuming call is fine, but an earlier escape isn't.
                prefix=part.clean[part.opening+1:window[-1].end]
                uses=list(re.finditer(rf'\b{store[1]}\b',prefix))
                if sum(not re.match(r'\s*\.',prefix[u.end():]) for u in uses)!=1:
                    continue
                member = store[1]+'.'+store[2]
                region = part.clean[node.start:window[-1].end]
                member_refs = list(re.finditer(rf'\b{store[1]}\s*\.\s*{store[2]}\b',region))
                if len(member_refs)!=1:  # no changed read/write ordering for this field
                    continue
                if any(variable in flow.nodes[i].live for i in window[-1].successors):
                    continue
                replacements = [(hit.start(),hit.end(),member), (copy.start,copy.end,'')]
                replacements += [(u.start(),u.end(),member) for u in refs(part,decl)
                                 if copy.end<=u.start()<window[-1].end]
                # The old scalar may be read before the copy too: forward those reads as well.
                replacements += [(u.start(),u.end(),member) for u in refs(part,decl)
                                 if hit.end()<=u.start()<copy.start]
                result.append((FAMILIES[0],edit(source,replacements)))
                if len(result)>=8:
                    return list(dict.fromkeys(result))
    result += sink_zero(source,name,flow)
    return list(dict.fromkeys(result))[:12]


def sink_zero(source, name, flow=None):
    """Sink a plain zero only when the other arm definitely assigns before reading it."""
    flow = flow or local_flow.analyze(source,name)
    if not flow:
        return []
    part = flow.part
    result = []
    # Full statement boundaries come from the same conservative CFG parser as liveness.
    for decl in locals_for(part):
        v = decl['name']
        pattern = rf'\b{v}\s*=\s*0\s*;\s*if\s*\('
        for hit in re.finditer(pattern, part.body):
            start=part.opening+1+hit.start()
            prefix=part.clean[part.opening+1:start].rstrip()
            if prefix and prefix[-1] not in ';{}':
                continue
            opening=part.opening+hit.end()
            end=close(part.clean,opening)
            if not end or re.search(rf'\b{v}\b',part.clean[opening+1:end-1]):
                continue
            body=flow.skip(end,part.end)
            if part.clean[body:body+1]!='{':
                continue
            stop=close(part.clean,body,'{','}')
            arm=part.clean[body+1:stop-1] if stop else ''
            # A single assignment in the true arm, no jumps/calls or hidden reads of v.
            assignment=re.fullmatch(rf'\s*{v}\s*=(?!=)([^;{{}}]+);\s*',arm)
            if not assignment or re.search(rf'\b{v}\b',assignment[1]):
                continue
            following=flow.skip(stop,part.end)
            if re.match(r'else\b',part.clean[following:]):
                continue
            zero_end=part.clean.find(';',start)+1
            result.append((FAMILIES[2],edit(source,[(start,zero_end,''),(stop,stop,f' else {{ {v}=0; }}')])))
    return result[:4]
