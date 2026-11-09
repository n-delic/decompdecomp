"""Typed pointer lifetime hypotheses and small declaration-order bridges, judged normally."""
import re

from ..forks.csource import _blank
from . import local_flow
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, split_args, view
from .pattern_repairs import close

POINTER = re.compile(rf'\b(?P<type>(?:struct\s+)?{IDENT}\s*\*+)\s*(?P<name>{IDENT})\s*;')
FAMILIES = ('pointer_load_inline', 'pointer_conditional_load_inline', 'pointer_call_staging', 'pointer_local_reuse', 'paired_pointer_declarations')
LIMIT = 24


def declarations(part, *, nested=False):
    result = []
    signature = part.clean[max(part.clean.rfind(ch,0,part.opening) for ch in ';{}')+1:part.opening]
    # A preceding macro's formal parameters are not function parameters.
    signature = re.sub(r'^\s*#.*$', '', signature, flags=re.M)
    for decl in POINTER.finditer(part.clean,part.opening+1,part.end):
        name = decl['name']
        prefix = part.clean[part.opening+1:decl.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':continue
        if not nested and block_path(part,decl.start()) != [part.opening]:continue
        # A uniquely declared local, no parameter shadow and no escaped storage.
        shadows=[m for m in re.finditer(rf'\b({IDENT})(?:\s+\**\s*|\s*\*+\s*){re.escape(name)}\s*(?=[;=,\[])',part.body)
                 if m[1] not in {'return','goto','case','break','continue','else','do'}]
        if len(shadows) != 1:continue
        if re.search(rf'(?<!&)&(?!&)\s*\(*\s*{re.escape(name)}\b',part.body):continue
        if re.search(rf'\b{re.escape(name)}\b',signature):continue
        result.append(decl)
    return result


def qualified_types(text):
    """Trace volatile aliases and nested record members without guessing field access widths."""
    qualified=set();dependencies=[]
    for alias in re.finditer(r'\btypedef\s+([^;{}]+?)\s+('+IDENT+r')\s*(?:\[[^;{}]*\])?\s*;',text):
        dependencies.append((alias[2],set(re.findall(IDENT,alias[1]))))
        if re.search(r'\bvolatile\b',alias[1]):qualified.add(alias[2])
    for record in re.finditer(r'\b(typedef\s+)?struct(?:\s+('+IDENT+r'))?\s*{',text):
        ending=close(text,record.end()-1,'{','}')
        if ending is None:continue
        names={record[2]} if record[2] else set()
        if record[1]:
            alias=re.match(r'\s*('+IDENT+r')\s*;',text[ending:])
            if alias:names.add(alias[1])
        body=text[record.end():ending-1]
        for name in names:
            dependencies.append((name,set(re.findall(IDENT,body))))
            if re.search(r'\bvolatile\b',body):qualified.add(name)
    changed=True
    while changed:
        changed=False
        for name,types in dependencies:
            if name not in qualified and types & qualified:qualified.add(name);changed=True
    return qualified


def context(source,name,header, *, nested=False):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|setjmp|longjmp)\b',part.clean):return None,[],{}
    combined=_blank(header)+'\n'+part.clean
    if re.search(r'::|\b(?:class|template|namespace)\b',combined):return None,[],{}
    qualified=qualified_types(combined)
    active=part.clean[max(part.clean.rfind(ch,0,part.opening) for ch in ';{}')+1:part.end]
    if qualified & set(re.findall(IDENT,active)):return None,[],{}
    pointers=declarations(part,nested=nested)
    local_names={d['name'] for d in pointers}|{d['name'] for d in locals_for(part)}
    # Follow invoked macro dependencies too: a macro can capture a local through another
    # macro. Liveness only sees invocation arguments, not hidden names or stringification.
    definitions={m[1]:(m[2],m[3]) for m in re.finditer(r'^\s*#\s*define\s+('+IDENT+r')(?:\(([^\n)]*)\))?[^\S\n]+([^\n]+)',combined,re.M)}
    pending=list(set(re.findall(IDENT,part.body)) & definitions.keys());seen=set()
    while pending:
        key=pending.pop()
        if key in seen:continue
        seen.add(key);params,body=definitions[key]
        free=set(re.findall(IDENT,body))-set(re.findall(IDENT,params or ''))
        if '#' in body or free & local_names:return None,[],{}
        pending.extend(free & definitions.keys())
    macros=definitions
    return part,pointers,macros


def pure(expression, macros, depth=0):
    """Expand only known, expression-only load macros to check for hidden calls/writes."""
    if depth>3 or re.search(r'[=;{}?:#]|\+\+|--|\b(?:volatile|sizeof|typeof)\b',expression):return False
    for key,(params,body) in macros.items():
        if params is None and re.search(rf'\b{re.escape(key)}\b',expression):
            if re.search(rf'\b{re.escape(key)}\s*\(',expression):return False
            expanded=re.sub(rf'\b{re.escape(key)}\b',lambda m, body=body:'('+body+')',expression)
            return pure(expanded,macros,depth+1)
    for hit in list(re.finditer(rf'\b({IDENT})\s*\(',expression)):
        if hit[1] not in macros:return False
        end=close(expression,hit.end()-1)
        if not end:return False
        params,body=macros[hit[1]]
        if params is None:return False
        args=[expression[hit.end()+a:hit.end()+b] for a,b in split_args(expression[hit.end():end-1])]
        params=[p.strip() for p in params.split(',')]
        if len(args)!=len(params) or not all(re.fullmatch(IDENT,p) for p in params):return False
        substitutions=dict(zip(params,args))
        expanded=re.sub(IDENT,lambda m, substitutions=substitutions:'('+substitutions[m[0]]+')' if m[0] in substitutions else m[0],body)
        if not pure(expanded,macros,depth+1):return False
    return True


def declaration_orders(source,name):
    part=view(source,name)
    if not part:return []
    ds=declarations(part)
    # Reorder only one contiguous group of plain, uninitialized pointer declarations.
    groups=[]
    for decl in ds:
        if groups and not part.clean[groups[-1][-1].end():decl.start()].strip():groups[-1].append(decl)
        else:groups.append([decl])
    out=[]
    for group in groups:
        if not 2<=len(group)<=8:continue
        types=sorted({d['type'] for d in group},reverse=True)
        orders=[[d for typ in types for d in sorted(group,key=lambda d:d['name']) if d['type']==typ]]
        for i in range(min(len(group)-1,4)):
            changed=list(group);changed[i],changed[i+1]=changed[i+1],changed[i];orders.append(changed)
        orders += [list(reversed(group)),sorted(group,key=lambda d:d['name'])]
        orders.append(sorted(group,key=lambda d:(''.join(d['type'].split()),d['name']),reverse=True))
        for order in orders:
            # Swap declaration tokens in place, retaining whitespace, line count and comments.
            text=edit(source,[(slot.start(),slot.end(),source[value.start():value.end()]) for slot,value in zip(group,order)])
            if text!=source and text not in out:out.append(text)
        if out:return out[:8]
    return out


def inline_loads(source,name,header=''):
    part,ds,macros=context(source,name,header)
    if not part:return []
    flow=local_flow.analyze(source,name,declarations=ds)
    if not flow:return []
    scalars={d['name'] for d in locals_for(part) if not re.search(rf'&\s*\(*\s*{re.escape(d["name"])}\b',part.body)}
    out=[]
    for decl in ds:
        variable=decl['name'];hits=refs(part,decl)
        assignments=[h for h in hits if re.match(r'\s*=(?!=)',part.clean[h.end():])]
        for assignment in assignments[:3]:
            tail=re.match(r'\s*=\s*([^;{}]+);',part.clean[assignment.end():])
            if not tail or not pure(tail[1],macros) or not ('*' in tail[1] or '->' in tail[1] or any(m+'(' in tail[1].replace(' ','') for m in macros)):continue
            stop=assignment.end()+tail.end();last=stop;uses=[]
            dependencies=set(re.findall(IDENT,tail[1]))
            for _ in range(4):
                statement=re.match(r'\s*('+IDENT+r')\s*=(?!=)([^;{}]+);',part.clean[last:part.end])
                if not statement or statement[1] not in scalars or statement[1] in dependencies or not pure(statement[2],macros):break
                end=last+statement.end();uses.extend(h for h in hits if last<=h.start()<end);last=end
            if not uses or block_path(part,assignment.start()) != block_path(part,uses[-1].start()):continue
            node=flow.at(last-1)
            if node is None or variable in set().union(*(flow.nodes[i].live for i in node.successors)):continue
            expr=source[assignment.end()+tail.start(1):assignment.end()+tail.end(1)]
            out.append(('pointer_load_inline',edit(source,[(assignment.start(),stop,'')]+[(h.start(),h.end(),'('+expr+')') for h in uses])))
            if len(out)==3:return out
    return out


def conditional_loads(source, name, header=''):
    """Inline a dead field-load temporary across its immediate null test only.

    No intervening statements or calls can change the field between its repeated
    reads. Qualified record types, escaped/shadowed locals and hidden macro uses
    are excluded by the same context as the other pointer families.
    """
    part, ds, macros = context(source, name, header)
    if not part:
        return []
    flow = local_flow.analyze(source, name, declarations=ds)
    if not flow:
        return []
    out = []
    for decl in ds:
        var = re.escape(decl['name'])
        pattern = re.compile(rf'(?<=[;{{}}])\s*{var}\s*=\s*(?P<base>{IDENT})\s*->\s*(?P<field>{IDENT})\s*;'
                             rf'\s*if\s*\(\s*{var}\s*!=\s*(?P<null>NULL|0)\s*\)\s*'
                             rf'(?P<brace>{{\s*)?(?P=base)\s*=\s*{var}\s*;')
        for hit in pattern.finditer(part.clean, decl.end()-1, part.end-1):
            if hit['base'] == decl['name'] or hit['base'] in macros or hit['field'] in macros:
                continue
            if hit['null'] in macros and not pure(hit['null'], macros):
                continue
            if hit['brace'] and not re.match(r'\s*}', part.clean[hit.end():]):
                continue
            uses = refs(part, decl)
            selected = [use for use in uses if hit.start() <= use.start() < hit.end()]
            node = flow.at(hit.end()-1)
            if (len(selected) != 3 or node is None or any(decl['name'] in flow.nodes[n].live for n in node.successors)):
                continue
            expr = f"{hit['base']}->{hit['field']}"
            replacement = f"\nif ({expr} != {hit['null']}) " + ('{ ' if hit['brace'] else '') + f"{hit['base']} = {expr};"
            out.append(('pointer_conditional_load_inline', edit(source, [(hit.start(), hit.end(), replacement)])))
    return out[:4]


def call_stages(source,name,header=''):
    part,ds,macros=context(source,name,header)
    if not part:return []
    # Only one unambiguous direct prototype returning an explicitly spelled pointer.
    prototypes={}
    prefix=_blank(header)+'\n'+part.clean[:part.opening]
    base=rf'(?:(?:unsigned|signed)\s+(?:char|short|int|long(?:\s+long)?)|long\s+long|(?:struct\s+)?{IDENT})'
    pointer=rf'(?:(?:const|volatile)\s+)*{base}\s*\*+'
    for hit in re.finditer(rf'(?:^|(?<=[;{{}}]))\s*(?:(?:extern|static)\s+)?({pointer})\s*({IDENT})\s*\(([^;{{}}()]*)\)\s*;',prefix):
        prototypes.setdefault(hit[2],[]).append(hit[1])
    flow=local_flow.analyze(source,name,declarations=ds)
    if not flow:return []
    out=[]
    for node in flow.nodes:
        statement=part.clean[node.start:node.end]
        outer=re.fullmatch(rf'\s*({IDENT})\s*\((.*)\)\s*;',statement,re.S)
        if not outer or outer[1] in macros:continue
        opening=node.start+statement.index('(')
        ending=close(part.clean,opening)
        if ending is None:continue
        for a,b in split_args(part.clean[opening+1:ending-1]):
            left,right=opening+1+a,opening+1+b
            arg=part.clean[left:right].strip()
            nested=re.fullmatch(rf'({IDENT})\s*\(([^;{{}}]*)\)',arg)
            if not nested or nested[1] in macros or len(prototypes.get(nested[1],[]))!=1:continue
            signature=part.clean[max(part.clean.rfind(ch,0,part.opening) for ch in ';{}')+1:part.opening]
            if re.search(rf'\b{re.escape(nested[1])}\b',signature) or re.search(rf'\(\s*\*\s*{re.escape(nested[1])}\b',part.body):continue
            if not pure(nested[2],macros):continue
            other=part.clean[opening+1:left]+part.clean[right:ending-1]
            if not pure(other,macros):continue
            typ=prototypes[nested[1]][0]
            outgoing=set().union(*(flow.nodes[i].live for i in node.successors))
            available=[d for d in ds if ''.join(d['type'].split())==''.join(typ.split()) and d.start()<node.start
                       and d['name'] not in node.live|outgoing and not re.search(rf'\b{d["name"]}\b',statement)]
            fresh='__repair_pointer_call'
            options=[(d['name'],'') for d in available[:2]]
            if fresh not in part.clean:options.append((fresh,typ+' '+fresh+';\n'))
            for variable,declaration in options:
                call=source[left:right].strip()
                text=source[node.start:node.end]
                text=text[:left-node.start]+variable+text[right-node.start:]
                # One block preserves unbraced control bodies. Plain declaration order stays C89.
                prefix=part.clean[part.opening+1:node.start].rstrip()
                content=variable+' = '+call+';\n'+text
                replacement=content if not declaration and (not prefix or prefix[-1] in ';{}') else '{\n'+declaration+content+'\n}'
                out.append(('pointer_call_staging',edit(source,[(node.start,node.end,replacement)])))
            if len(out)>=6:return out[:6]
    return out


def initialized(flow, variable):
    if flow.entry is None:return False
    reachable=set();pending=[flow.entry]
    while pending:
        index=pending.pop()
        if index in reachable:continue
        reachable.add(index);pending.extend(flow.nodes[index].successors)
    predecessors={i:set() for i in reachable}
    for i in reachable:
        for successor in flow.nodes[i].successors:predecessors[successor].add(i)
    outgoing={i:True for i in reachable};incoming={i:True for i in reachable}
    changed=True
    while changed:
        changed=False
        for i in sorted(reachable):
            node=flow.nodes[i]
            prior=i!=flow.entry and bool(predecessors[i]) and all(outgoing[p] for p in predecessors[i])
            text=flow.part.clean[node.start:node.end]
            assigned=bool(re.fullmatch(rf'\s*{re.escape(variable)}\s*=(?!=).*;\s*',text,re.S))
            after=assigned if variable in node.defines else prior
            if incoming[i]!=prior or outgoing[i]!=after:changed=True
            incoming[i],outgoing[i]=prior,after
    return all(variable not in flow.nodes[i].uses or incoming[i] for i in reachable)


def reuse(source,name,header=''):
    part,ds,_=context(source,name,header)
    if not part:return []
    flow=local_flow.analyze(source,name,declarations=ds)
    if not flow:return []
    ds=[d for d in ds if initialized(flow,d['name'])]
    out=[]
    for index,left in enumerate(ds):
        for right in ds[index+1:]:
            if ''.join(left['type'].split())!=''.join(right['type'].split()) or not local_flow.disjoint(flow,left,right):continue
            if not refs(part,left) or not refs(part,right):continue
            out.append(('pointer_local_reuse',edit(source,[(right.start(),right.end(),'')]+[(h.start(),h.end(),left['name']) for h in refs(part,right)])))
            if len(out)==3:return out
    return out


def variants(source,name,header=''):
    singles=inline_loads(source,name,header)+conditional_loads(source,name,header)+call_stages(source,name,header)+reuse(source,name,header)
    pools=[]
    for family,text in singles[:6]:
        pools.append([(FAMILIES[-1],candidate) for candidate in declaration_orders(text,name)])
    result=list(singles[:8])
    while any(pools) and len(result)<LIMIT:
        for pool in pools:
            if pool and len(result)<LIMIT:result.append(pool.pop(0))
    return list(dict.fromkeys(result))
