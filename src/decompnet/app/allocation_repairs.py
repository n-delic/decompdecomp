"""Bounded lifetime, spill-order and byte-fill scheduling hypotheses; judged normally.

No artificial stack fields, tautological guards or prescribed machine registers. Unsupported
syntax is left alone. The existing CFG split/reuse families supply phase partition candidates.
"""
import re
from itertools import permutations, zip_longest

from . import pointer_pair_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, refs, split_args, view
from .pattern_repairs import close

FAMILIES = ('spill_declaration_order','pointer_argument_alias','byte_fill_constant','allocation_schedule_bridge','allocation_initialization_order')
LIMIT = 24


def declaration_orders(source,name,header=''):
    part,pointers,_ = pointer_pair_repairs.context(source,name,header)
    if not part:return []
    ds = sorted(pointers+locals_for(part),key=lambda d:d.start())
    groups = []
    for d in ds:
        if block_path(part,d.start()) != [part.opening]:continue
        if groups and not part.clean[groups[-1][-1].end():d.start()].strip():groups[-1].append(d)
        else:groups.append([d])
    out = []
    for group in groups:
        if not 2 <= len(group) <= 12 or not any('*' in d['type'] for d in group):continue
        # Move each pointer through a mixed scalar/pointer declaration block, not a factorial search.
        for d in [d for d in group if '*' in d['type']][:3]:
            for position in (0,len(group)-1):
                order = [x for x in group if x is not d];order.insert(position,d)
                candidate = edit(source,[(a.start(),a.end(),source[b.start():b.end()]) for a,b in zip(group,order)])
                if candidate!=source and candidate not in out:out.append(candidate)
        if out:break
    return out[:6]


def pointer_aliases(source,name,header=''):
    part,pointers,macros = pointer_pair_repairs.context(source,name,header)
    fresh = '__repair_argument_pointer'
    if not part or fresh in source+header or re.search(r'\b(?:goto|case|default)\b',part.body):return []
    out = []
    for decl in pointers:
        hits = refs(part,decl);var = decl['name']
        writes = [h for h in hits if re.match(r'\s*(?:=(?!=)|[+\-*/%&|^]=|\+\+|--)',part.clean[h.end():])
                  or re.search(r'(?:\+\+|--)\s*$',part.clean[:h.start()])]
        if len(writes)!=1:continue
        assignment = writes[0]
        match = re.match(r'\s*=\s*&\s*'+IDENT+r'(?:\s*\.\s*'+IDENT+r')*\s*;',part.clean[assignment.end():])
        if not match or block_path(part,assignment.start()) != [part.opening]:continue
        prefix = part.clean[part.opening+1:assignment.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':continue
        initialized = assignment.end()+match.end()
        uses = []
        for call in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(',part.body):
            if call[1] in macros or call[1] in {'if','for','while','switch','sizeof'}:continue
            start = part.opening+1+call.start();opening = part.opening+call.end()
            end = close(part.clean,opening)
            if start < initialized or end is None or not re.match(r'\s*;',part.clean[end:]):continue
            for a,b in split_args(part.clean[opening+1:end-1]):
                left,right = opening+1+a,opening+1+b
                if part.clean[left:right].strip()==var:
                    uses.append((left,right,fresh))
        if len(uses)<2:continue
        # Initialize immediately after the proven outer-scope assignment. Replacing only direct
        # arguments leaves aggregate destinations direct and preserves every call/evaluation.
        out.append(edit(source,[(part.opening+1,part.opening+1,'\n'+decl['type']+' '+fresh+';'),
                                (initialized,initialized,'\n'+fresh+' = '+var+';')]+uses))
        if len(out)==3:break
    return out


def byte_constants(source,name,header=''):
    part,_,macros = pointer_pair_repairs.context(source,name,header)
    fresh = '__repair_fill_one'
    if not part or fresh in source+header:return []
    pointers = set(re.findall(rf'\b(?:s8|u8|char|signed\s+char|unsigned\s+char)\s*\*\s*({IDENT})\s*;',part.body))
    out = []
    # A single-statement fill loop: the added scalar has a representable constant, no narrowing
    # of existing arithmetic and no duplication of a pointer evaluation.
    pattern = rf'\bdo\s*{{\s*\*\s*\(\s*({IDENT})\s*\+\+\s*\)\s*=\s*1\s*;\s*}}\s*while\s*\('
    for hit in re.finditer(pattern,part.body):
        if hit[1] not in pointers or hit[1] in macros:continue
        start = part.opening+1+hit.start();opening = part.opening+hit.end()
        end = close(part.clean,opening)
        suffix = re.match(r'\s*;',part.clean[end:]) if end else None
        if not suffix:continue
        end += suffix.end()
        loop = source[start:end]
        loop = re.sub(r'=\s*1\s*;','= '+fresh+';',loop,count=1)
        for typ in ('signed char','unsigned char'):
            out.append(edit(source,[(start,end,'{ '+typ+' '+fresh+' = 1; '+loop+' }')]))
        if len(out)>=4:break
    return out


def initialization_orders(source,name,header=''):
    part = view(source,name)
    if not part or re.search(r'\b(?:asm|__asm__)\b',part.clean):return []
    # Volatile accesses elsewhere do not forbid moving two plain local stores. Do not
    # read/move volatile expressions themselves or infer semantics for opaque macros.
    from .allocator_call_guards import declarations
    declared = {d['name']:d for d in declarations(part)+locals_for(part)}
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')',source+'\n'+header,re.M))
    arrays = set(re.findall(rf'\b(?:char|[su](?:8|16|32)|int)\s+({IDENT})\s*\[\s*{NUMBER}\s*\]',part.body))
    array_atom = '|'.join(map(re.escape,sorted(arrays))) or r'(?!)'
    atom = rf'(?:{NUMBER}|&\s*{IDENT}(?:\s*\.\s*{IDENT})*|{array_atom})'
    hits = list(re.finditer(rf'\b({IDENT})\s*=\s*({atom})\s*;',part.body))
    out = []
    for left,right in zip(hits,hits[1:]):
        if part.body[left.end():right.start()].strip():continue
        start,mid,end = (part.opening+1+left.start(),part.opening+1+right.start(),part.opening+1+right.end())
        a,b = left[1],right[1]
        prefix = part.clean[part.opening+1:start].rstrip()
        if prefix and prefix[-1] not in ';{}':continue
        if a==b or a not in declared or b not in declared:continue
        if any(declared[v].end()>start for v in (a,b)):continue
        if {a,b} & set(re.findall(IDENT,left[2]+' '+right[2])):continue
        if set(re.findall(IDENT,left[0]+' '+right[0])) & macros:continue
        left_end = part.opening+1+left.end()
        out.append(edit(source,[(start,left_end,source[mid:end]),(mid,end,source[start:left_end])]))
    # Permute a small, contiguous group together: adjacent swaps alone can plateau
    # when a pointer, loop counter and narrow scalar compete in the scheduler.
    groups = []
    for hit in hits:
        if groups and not part.body[groups[-1][-1].end():hit.start()].strip():
            groups[-1].append(hit)
        else:
            groups.append([hit])
    clustered = []
    for group in groups:
        if len(group)>4:
            windows = [group[i:i+4] for i in range(len(group)-3)]
            group = max(windows,key=lambda items:sum(h[2].lstrip().startswith('&') or h[2].strip() in arrays for h in items))
        if not 3 <= len(group) <= 4:
            continue
        start = part.opening+1+group[0].start()
        names = [hit[1] for hit in group]
        prefix = part.clean[part.opening+1:start].rstrip()
        if (prefix and prefix[-1] not in ';{}') or len(set(names)) != len(names):
            continue
        if any(v not in declared or declared[v].end()>start for v in names):
            continue
        if set(names) & set(re.findall(IDENT, ' '.join(hit[2] for hit in group))):
            continue
        if set(re.findall(IDENT, ' '.join(hit[0] for hit in group))) & macros:
            continue
        for order in permutations(group):
            if list(order) == group:
                continue
            # Keep comments and line slots in place; do not replace the group's gaps.
            clustered.append(edit(source,[(part.opening+1+a.start(),part.opening+1+a.end(),
                                          source[part.opening+1+b.start():part.opening+1+b.end()])
                                         for a,b in zip(group,order)]))
        break
    return list(dict.fromkeys(clustered+out[-6:]))[:24]


def variants(source,name,header=''):
    pools = [[(FAMILIES[0],s) for s in declaration_orders(source,name,header)],
             [(FAMILIES[1],s) for s in pointer_aliases(source,name,header)],
             [(FAMILIES[2],s) for s in byte_constants(source,name,header)],
             [(FAMILIES[4],s) for s in initialization_orders(source,name,header)]]
    singles = [item for row in zip_longest(*pools) for item in row if item is not None]
    # Small bridges expose allocation changes that individually plateau. Initialization-order
    # variants remain in the shared scheduling families and subsequent iterative search stages.
    result = list(singles)
    for _,parent in singles[:4]:
        for child in declaration_orders(parent,name,header)[:2]:
            result.append((FAMILIES[3],child))
    return list(dict.fromkeys(result))[:LIMIT]
