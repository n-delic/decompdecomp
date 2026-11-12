"""Debug-line grouping and repeated float-literal staging, without reordering existing effects."""
import re

from . import local_flow, pointer_pair_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, view

FAMILIES = ('debug_initialization_group', 'debug_constant_initialization')
FLOAT = re.compile(r'(?<![\w.])(?:[0-9]+\.[0-9]*(?:[eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)[fF]\b')
LIMIT = 16


def variants(source,name,flags=(),header=''):
    debug = [f for f in flags if f=='-g' or re.fullmatch(r'-g[0-3]',f)]
    if not debug or debug[-1]=='-g0':
        return []
    if '__LINE__' in source+'\n'+header or re.search(r'\\\n|^\s*#\s*line\b',source,re.M):
        return []
    part = view(source,name)
    flow = local_flow.analyze(source,name)
    if not part or not flow:
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    assignments = sorted((n for n in flow.nodes if re.fullmatch(r'\s*'+IDENT+r'\s*=(?!=)[^;{}]+;\s*',part.clean[n.start:n.end])),key=lambda n:n.start)
    groups = []
    for i,node in enumerate(assignments):
        group = [node]
        for other in assignments[i+1:i+4]:
            if (part.clean[group[-1].end:other.start].strip() or
                    block_path(part,node.start)!=block_path(part,other.start)):
                break
            group.append(other)
        if len(group)>=2:
            groups.append(group)
    declared = pointer_pair_repairs.declarations(part)+[d for d in locals_for(part) if block_path(part,d.start())==[part.opening]]
    float_decls = list(re.finditer(r'\b(?P<type>float|double|f32|f64)\s+(?P<name>'+IDENT+r')\s*;',part.clean[part.opening+1:part.end-1]))
    qualified = pointer_pair_repairs.qualified_types(header+'\n'+source)
    private = {d['name']:d.end() for d in declared if not set(re.findall(IDENT,d['type'])) & qualified}
    for decl in float_decls:
        v = decl['name']
        if (decl['type'] not in qualified and sum(d['name']==v for d in float_decls)==1
                and block_path(part,part.opening+1+decl.start())==[part.opening]):
            private[v] = part.opening+1+decl.end()
    private = {v:end for v,end in private.items() if not re.search(r'&\s*\(*\s*'+v+r'\b',part.body)}
    result = []
    literals = list(dict.fromkeys(m[0] for m in FLOAT.finditer(part.body)))
    literals = [lit for lit in literals if sum(m[0]==lit for m in FLOAT.finditer(part.body))>=2][:3]
    for group in groups[:4]:
        start,end = group[0].start,group[-1].end
        original = source[start:end]
        clean = part.clean[start:end]
        # Joining physical lines must not turn a line comment into swallowed code or
        # change macro invocation semantics. Existing statement order remains intact.
        if ('//' in original or '/*' in original or set(re.findall(IDENT,clean)) & macros
                or re.search(r'\b'+IDENT+r'\s*\(',clean)):
            continue
        joined = re.sub(r'\s*\n\s*',' ',original)
        orders = [joined]
        assignments_in_group = [re.fullmatch(r'\s*('+IDENT+r')\s*=(?!=)([^;]+);\s*',part.clean[n.start:n.end]) for n in group]
        names = [m[1] for m in assignments_in_group]
        tail_names = set(names[1:])
        allowed = set(private) | {'char','float','double','f32','f64','u8','u16','u32','s8','s16','s32'}
        tails_pure = all((re.fullmatch(r'\s*&\s*'+IDENT+r'\s*',m[2]) or
                          (set(re.findall(IDENT,m[2])) <= allowed and
                           not re.search(r'->|\[|(?<!\w)\*\s*\(',m[2]))) for m in assignments_in_group[1:])
        if (len(set(names))==len(names) and all(v in private and private[v]<=start for v in names) and tails_pure
                and not set(re.findall(IDENT,assignments_in_group[0][2])) & tail_names
                and not any(names[0] in set(re.findall(IDENT,m[2])) for m in assignments_in_group[1:])
                and not re.search(r'\bvolatile\b',source)):
            rotated = ' '.join(re.sub(r'\s*\n\s*',' ',source[n.start:n.end]) for n in group[1:]+group[:1])
            orders.append(rotated)
        for order in orders:
            if order!=original:
                result.append((FAMILIES[0],edit(source,[(start,end,order)])))
        # Constants are values, not memory reads. Stage only in a top-level run;
        # no speculative load or existing statement is moved across a branch.
        if block_path(part,start)!=[part.opening]:
            continue
        fresh = '__repair_float_constant'
        if re.search(r'\b'+fresh+r'\b',source+'\n'+header):
            continue
        for literal in literals:
            uses = [m for m in FLOAT.finditer(part.clean,part.opening+1,part.end) if m[0]==literal]
            if any(m.start()<end for m in uses):
                continue
            edits = [(part.opening+1,part.opening+1,' float '+fresh+'; ')]
            edits += [(m.start(),m.end(),fresh) for m in uses]
            # All literal uses follow this run, so replacement offsets do not overlap.
            for order in orders:
                staged = edit(source,edits+[(start,end,fresh+' = '+literal+'; '+order)])
                result.append((FAMILIES[1],staged))
    return list(dict.fromkeys(result))[:LIMIT]
