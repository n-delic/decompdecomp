"""Selective branch pointer lifetimes and bounded declaration bridges, judged normally.

Distribute only a void terminal suffix after a top-level switch whose cases cannot fall through.
The CFG proves initialized pointers at every read. Unsupported control and opaque macros stay unchanged.
"""
import re
from itertools import combinations

from . import allocation_repairs, local_flow, pointer_pair_repairs
from .branch_layout_repairs import controls, terminal_case
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, split_args, view
from .pattern_repairs import close

FAMILIES = ('branch_pointer_lifetimes','branch_pointer_declarations')
LIMIT = 66


def expand_statements(source,name,header=''):
    """Inline bounded statement macros only with expression arguments free of side effects."""
    definitions={}
    text=re.sub(r'\\\n',' ',header+'\n'+source)
    for m in re.finditer(r'^\s*#\s*define\s+('+IDENT+r')\(([^)]*)\)[ \t]+([^\n]+)',text,re.M):
        params=[x.strip() for x in m[2].split(',')]
        if not all(re.fullmatch(IDENT,x) for x in params) or len(set(params))!=len(params):continue
        body=m[3].strip()
        if not body.endswith(';') or any(c in body for c in ('"',"'",'/*','//')) or re.search(r'[#{}]|\b(?:if|for|while|do|goto|return|break|continue|volatile)\b',body):continue
        definitions[m[1]]=(params,body)
    for _ in range(64):
        part=view(source,name)
        if not part:return None
        changed=False
        for hit in re.finditer(r'\b('+IDENT+r')\s*\(',part.body):
            if hit[1] not in definitions:continue
            start=part.opening+1+hit.start();opening=part.opening+hit.end()
            prefix=part.clean[part.opening+1:start].rstrip()
            if prefix and prefix[-1] not in ';{}':continue
            end=close(part.clean,opening)
            if not end:return None
            text=part.clean[opening+1:end-1]
            args=[text[a:b].strip() for a,b in split_args(text)]
            params,body=definitions[hit[1]]
            if len(args)!=len(params) or any(re.search(r'[=;{}#]|\+\+|--|\b'+IDENT+r'\s*\(',a) for a in args):return None
            substitutions=dict(zip(params,args))
            replacement=re.sub(IDENT,lambda m, substitutions=substitutions:substitutions.get(m[0],m[0]),body)
            source=edit(source,[(start,end,replacement)]);changed=True;break
        if not changed:return source
    return None


def distribute(source,name):
    part=view(source,name)
    if not part or not re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\([^;{}]*\)\s*$',part.clean[:part.opening]):return None
    part.end=len(part.clean[:part.end].rstrip())
    if re.search(r'\b(?:goto|for|while|do|asm|__asm__|volatile)\b',part.body):return None
    switches=[]
    for hit in re.finditer(r'\bswitch\s*\(',part.body):
        start=part.opening+1+hit.start()
        if block_path(part,start)!=[part.opening]:continue
        end=close(part.clean,part.opening+hit.end())
        arm=re.match(r'\s*{',part.clean[end:]) if end else None
        if not arm:continue
        opening=end+arm.end()-1;ending=close(part.clean,opening,'{','}')
        if ending:switches.append((start,opening,ending))
    if len(switches)!=1:return None
    _,opening,ending=switches[0]
    # A copied suffix must not bind to a branch-local name that shadowed its original scope.
    interior=part.clean[opening+1:ending-1]
    declaration=rf'(?:^|[;{{}}])\s*(?:(?:const|volatile|unsigned|signed|struct)\s+)*{IDENT}(?:\s+|\s*\*+\s*){IDENT}\s*(?:[;=,\[])'
    if re.search(declaration,interior) or re.search(r'\b(?:typedef|struct|union)\b',interior):return None
    suffix=part.clean[ending:part.end-1]
    if not suffix.strip() or re.search(r'[{}]|\b(?:return|if|switch|case|break|continue)\b',suffix):return None
    if not 1<=suffix.count(';')<=6:return None
    # Require a default return and terminal breaks/returns in every case: no fallthrough or
    # path from switch dispatch that originally skipped the copied suffix.
    labels=[m for m in re.finditer(r'\b(?:case\s+[^:;{}]+|default)\s*:',part.clean[opening+1:ending-1])
            if block_path(part,opening+1+m.start())==[part.opening,opening]]
    if not labels or not any(m[0].startswith('default') for m in labels):return None
    ranges=[]
    for i,label in enumerate(labels):
        a=opening+1+label.end();b=opening+1+labels[i+1].start() if i+1<len(labels) else ending-1
        if not terminal_case(part.clean[a:b]):return None
        ranges.append((a,b))
    scopes=controls(part);leaves=[]
    # An innermost braced if/else arm must be straight-line, ending by closing into a case break.
    for right,(left,kind) in scopes.items():
        if kind not in {'if','else'} or not opening<left<right<ending:continue
        body=part.clean[left+1:right]
        if re.search(r'[{}]|\b(?:return|break|continue|case|default)\b',body):continue
        if not body.strip() or not body.rstrip().endswith(';'):continue
        leaves.append(right)
    # Every non-return case must have terminal leaf coverage in both arms, including else-if.
    # Rather than guessing coverage lexically, verify each CFG edge into the suffix is a break,
    # and each backwards path through that break ends at a selected leaf's last statement.
    flow=local_flow.analyze(source,name)
    if not flow:return None
    entry=min((i for i,n in enumerate(flow.nodes) if n.start>=ending and part.clean[n.start:n.end].strip()),key=lambda i:flow.nodes[i].start,default=None)
    if entry is None:return None
    parents={i:[] for i in range(len(flow.nodes))}
    for i,node in enumerate(flow.nodes):
        for j in node.successors:parents[j].append(i)
    predecessors=parents[entry]
    if not predecessors:return None
    covered=set()
    for index in predecessors:
        node=flow.nodes[index]
        if part.clean[node.start:node.end].strip()!='break;':return None
        for prior in parents[index]:
            n=flow.nodes[prior]
            closing=next((r for r in leaves if n.end<=r and not part.clean[n.end:r].strip()),None)
            if closing is None:return None
            covered.add(closing)
    if not 2<=len(covered)<=12:return None
    tail=source[ending:part.end-1]
    return edit(source,[(ending,part.end-1,'')]+[(p,p,tail) for p in sorted(covered)])


def localize(source,name,header=''):
    part,ds,_=pointer_pair_repairs.context(source,name,header)
    if not part:return []
    scopes=controls(part);options=[]
    for decl in ds:
        variable=decl['name'];hits=refs(part,decl)
        flow=local_flow.analyze(source,name,declarations=[decl])
        if not hits or not flow or not pointer_pair_repairs.initialized(flow,variable):continue
        groups={}
        for hit in hits:
            containing=[(a,b) for b,(a,kind) in scopes.items() if kind in {'if','else'} and a<hit.start()<b]
            if not containing:break
            # Use leaf scopes; nested definitions/reads that require overlapping scopes are rejected.
            a,b=max(containing,key=lambda pair:pair[0]);groups.setdefault((a,b),[]).append(hit)
        else:
            if not 2<=len(groups)<=12:continue
            # Distinct chosen scopes must not nest or overlap.
            spans=sorted(groups)
            if any(b>=c for (_,b),(c,_) in zip(spans,spans[1:])):continue
            options.append((decl,spans))
    result=[]
    selections=[(x,) for x in options[:3]]
    selections+=list(combinations(options[:3],2))
    if 2<len(options)<=3:selections.append(tuple(options))
    for chosen in selections:
        edits=[]
        for decl,spans in chosen:
            edits.append((decl.start(),decl.end(),''))
            for a,b in spans:edits.append((a+1,a+1,'\n'+source[decl.start():decl.end()]+'\n'))
        result.append(edit(source,edits))
    return list(dict.fromkeys(result))


def variants(source,name,header=''):
    expanded=expand_statements(source,name,header)
    if expanded is None:return []
    parents=[expanded]
    distributed=distribute(expanded,name)
    if distributed:parents.append(distributed)
    singles=[]
    for parent in parents:
        singles += [(FAMILIES[0],s) for s in localize(parent,name,header)]
    out=list(singles)
    pools=[]
    for _,parent in singles[:3]:
        part=view(parent,name)
        ds=[d for d in locals_for(part) if block_path(part,d.start())==[part.opening]]
        pool=[]
        for a,b in combinations(ds[:10],2):
            if a['type']!=b['type']:continue
            # Reorder uninitialized declaration tokens only, preserving all whitespace/comments.
            between=[d for d in ds if a.end()<=d.start()<b.start()]
            cursor=a.end();valid=True
            for d in between:
                if part.clean[cursor:d.start()].strip():valid=False;break
                cursor=d.end()
            if not valid or part.clean[cursor:b.start()].strip():continue
            pool.append((FAMILIES[1],edit(parent,[(a.start(),a.end(),parent[b.start():b.end()]),
                                                  (b.start(),b.end(),parent[a.start():a.end()])])))
        pool += [(FAMILIES[1],s) for s in allocation_repairs.declaration_orders(parent,name,header)[:2]]
        pools.append(pool[:21])
    from itertools import zip_longest
    for row in zip_longest(*pools):
        out.extend(item for item in row if item is not None)
    return list(dict.fromkeys(out))[:LIMIT]
