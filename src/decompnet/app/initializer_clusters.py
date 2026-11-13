"""Expose pure comma initializers as statements and join a nearby independent address initializer."""
import re

from decomprepair import allocation_repairs, local_flow
from decomprepair.allocator_call_guards import context, declarations, dominators
from decomprepair.call_lifetime_repairs import block_path
from decomprepair.integer_repair_support import IDENT, NUMBER, edit, locals_for, split_args
from decomprepair.pattern_repairs import close

FAMILY = 'initialization_cluster'


def variants(source,name,header=''):
    part,_,macros = context(source,name,header)
    if not part:
        return []
    pointer_decls = declarations(part)
    declared = {d['name']:d for d in pointer_decls+locals_for(part)}
    flow = local_flow.analyze(source,name,declarations=list(declared.values()))
    dom = dominators(flow) if flow else {}
    arrays = set(re.findall(rf'\b(?:char|[su](?:8|16|32)|int)\s+({IDENT})\s*\[\s*{NUMBER}\s*\]',part.body))
    array_atom = '|'.join(map(re.escape,sorted(arrays))) or r'(?!)'
    assignment = rf'\s*({IDENT})\s*=\s*({NUMBER}|&\s*{IDENT}|{array_atom})\s*'
    out = []
    for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(\s*\(',part.body):
        if hit[1] in macros or hit[1] in {'if','while','for','switch','sizeof'}:
            continue
        call_start = part.opening+1+hit.start()
        inner = part.opening+hit.end()
        end = close(part.clean,inner)
        if not end:
            continue
        pieces = [part.clean[inner+1+a:inner+1+b] for a,b in split_args(part.clean[inner+1:end-1])]
        if not 3<=len(pieces)<=5 or not re.fullmatch(rf'\s*{IDENT}\s*',pieces[-1]):
            continue
        items = [re.fullmatch(assignment,p) for p in pieces[:-1]]
        if not all(items):
            continue
        names = {m[1] for m in items}
        if len(names)!=len(items) or any(v not in declared or declared[v].end()>call_start for v in names):
            continue
        if names & set(re.findall(IDENT,' '.join(m[2] for m in items))) or names & set(re.findall(IDENT,pieces[-1])):
            continue
        call_open = part.clean.find('(',call_start,inner)
        call_end = close(part.clean,call_open)
        if not call_end or not re.match(r'\s*;',part.clean[call_end:]):
            continue
        rest = part.clean[end:call_end]
        if (names & set(re.findall(IDENT,rest)) or set(re.findall(IDENT,' '.join(pieces)+rest)) & set(macros)
                or re.search(r'\+\+|--|=(?!=)|\b'+IDENT+r'\s*\(',rest)):
            continue
        statements = '\n'.join(m[1]+' = '+m[2]+';' for m in items)+'\n'
        base = edit(source,[(inner,end,pieces[-1]),(call_start,call_start,statements)])
        out.append((FAMILY,base))
        # Delay a constant address until after the last reached call when the call
        # dominates every use of that unescaped pointer. Calls cannot inspect its
        # private storage; taking the same array address reads no object contents.
        call_index = next((i for i,n in enumerate(flow.nodes) if n.start<=part.opening+1+hit.start(1)<n.end),None) if flow else None
        if call_index is not None:
            for decl in pointer_decls[:8]:
                variable = decl['name']
                if variable in names or any(variable in set(re.findall(IDENT,body)) for _,body in macros.values()):
                    continue
                writes = [i for i,n in enumerate(flow.nodes) if variable in n.defines and not n.start<=decl.start()<n.end]
                uses = [i for i,n in enumerate(flow.nodes) if variable in n.uses]
                if len(writes)!=1 or writes[0] not in dom.get(call_index,set()) or not uses:
                    continue
                initial = flow.nodes[writes[0]]
                text = part.clean[initial.start:initial.end]
                if not re.fullmatch(rf'\s*{variable}\s*=\s*&\s*{IDENT}\s*;',text):
                    continue
                if any(call_index not in dom.get(i,set()) for i in uses):
                    continue
                parents = {i:set() for i in dom}
                for i in dom:
                    for child in flow.nodes[i].successors & set(dom):parents[child].add(i)
                current, anchor, visited = call_index, call_start, set()
                while len(parents[current])==1 and current not in visited and len(visited)<16:
                    visited.add(current)
                    previous = next(iter(parents[current]))
                    node = flow.nodes[previous]
                    if node.start<=initial.end or len(node.successors)!=1:
                        break
                    if not re.fullmatch(rf'\s*{IDENT}\s*=(?!=)[^;{{}}]+;',part.clean[node.start:node.end]):
                        break
                    if re.search(r'\b'+IDENT+r'\s*\(',part.clean[node.start:node.end]):
                        break
                    anchor,current = node.start,previous
                if block_path(part,initial.start)!=block_path(part,anchor):
                    continue
                operand = re.search(r'&\s*('+IDENT+r')',text)[1]
                if re.search(rf'\b{IDENT}\s+\**\s*{operand}\s*(?=[;=,\[])',part.clean[initial.end:anchor]):
                    continue
                moved = edit(source,[(initial.start,initial.end,''),(inner,end,pieces[-1]),
                                     (anchor,anchor,source[initial.start:initial.end]+'\n'+statements)])
                out.append((FAMILY,moved))
                out.extend((FAMILY,c) for c in allocation_repairs.initialization_orders(moved,name,header))
                break
        # One preceding pure pointer-address assignment may join this group across
        # independent scalar arithmetic, but never a label, branch, load or call.
        before = part.clean[part.opening+1:call_start]
        for prior in list(re.finditer(rf'\b({IDENT})\s*=\s*(&\s*{IDENT})\s*;',before))[-3:]:
            variable = prior[1]
            if variable not in declared or variable in names:
                continue
            gap = before[prior.end():]
            if re.search(r'[{}:]|\b(?:goto|if|while|for|do|return)\b|\+\+|--|\b'+IDENT+r'\s*\(',gap):
                continue
            if re.search(rf'\b{variable}\b',gap) or re.search(r'\[|->|\*\s*\(',gap):
                continue
            start = part.opening+1+prior.start()
            assignments = list(re.finditer(rf'\b({IDENT})\s*=(?!=)[^;{{}}]+;',gap))
            if re.sub(rf'\b{IDENT}\s*=(?!=)[^;{{}}]+;','',gap).strip():
                continue
            if any(a[1] not in declared or declared[a[1]].end()>start for a in assignments):
                continue
            prefix = part.clean[part.opening+1:start].rstrip()
            if prefix and prefix[-1] not in ';{}':
                continue
            if set(re.findall(IDENT,prior[0]+gap)) & set(macros):
                continue
            if not names & set(re.findall(IDENT,gap)) and all(declared[v].end()<start for v in names):
                lifted = edit(source,[(start,start,statements),(inner,end,pieces[-1])])
                out.append((FAMILY,lifted))
                out.extend((FAMILY,c) for c in allocation_repairs.initialization_orders(lifted,name,header))
            # No access to the pointer's storage escapes (declarations() enforces it).
            moved = edit(source,[(start,part.opening+1+prior.end(),''),
                                 (inner,end,pieces[-1]),
                                 (call_start,call_start,statements+source[start:part.opening+1+prior.end()]+'\n')])
            out.append((FAMILY,moved))
            out.extend((FAMILY,c) for c in allocation_repairs.initialization_orders(moved,name,header))
            break
        out.extend((FAMILY,c) for c in allocation_repairs.initialization_orders(base,name,header))
        if len(out)>=48:
            break
    return list(dict.fromkeys(out))[:48]
