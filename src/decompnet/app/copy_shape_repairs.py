"""Bounded copy/command source shapes; normal compilation and judging remain mandatory."""
import re

from decomprepair.integer_repair_support import IDENT, edit, view

FAMILIES = ('inline_copy_loop', 'typed_command_record')
WORD = r'(?:u32|unsigned\s+int)'


def declaration_counts(body):
    """Count simple declarators in every scope, including comma-separated shadows."""
    counts = {}
    pattern = rf'\b(?:unsigned\s+(?:int|short|char)|signed\s+(?:int|short|char)|{IDENT})\s+([^;{{}}()]+);'
    for decl in re.finditer(pattern,body):
        if decl[0].split()[0] in {'return','goto','else'}:
            continue
        for item in decl[1].split(','):
            m=re.fullmatch(rf'\s*\*?\s*({IDENT})(?:\s*=.*)?\s*',item)
            if m:counts[m[1]]=counts.get(m[1],0)+1
    return counts


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|restrict|__restrict)\b|^\s*#', part.clean, re.M):
        return []
    counts = declaration_counts(part.body)
    out = copy_branches(source, name, header)
    # Local pointers must not escape: helper-local induction is valid even for overlapping buffers.
    # Write the final pointer/counter values back explicitly, preserving subsequent caller uses.
    pointers = set()
    for decl in re.finditer(rf'\b{WORD}\s+([^;{{}}()]+);', part.body):
        for item in decl[1].split(','):
            m = re.fullmatch(rf'\s*\*\s*({IDENT})\s*', item)
            if (m and not re.search(rf'&\s*\(*\s*{m[1]}\b', part.body)
                and counts.get(m[1])==1):
                pointers.add(m[1])
    loop = re.compile(rf'for\s*\(\s*({IDENT})\s*=\s*([1-9][0-9]?)\s*;\s*\1\s*!=\s*0\s*;\s*--\s*\1\s*\)\s*{{\s*(?:\*\s*({IDENT})\+\+\s*=\s*\*\s*({IDENT})\+\+\s*;|({IDENT})--\s*;\s*({IDENT})--\s*;\s*\*\s*({IDENT})\s*=\s*\*\s*({IDENT})\s*;)\s*}}')
    groups = {}
    for hit in loop.finditer(part.body):
        counter, count = hit[1], int(hit[2])
        dest, src = (hit[3], hit[4]) if hit[3] else (hit[7], hit[8])
        if not hit[3] and ({hit[5], hit[6]} != {dest, src} or hit[5] == hit[6]):
            continue
        if count > 32 or dest == src or not {dest, src} <= pointers:
            continue
        if len(re.findall(rf'\b(?:s32|int)\s+[^;{{}}]*\b{counter}\b[^;{{}}]*;', part.body)) != 1:
            continue
        if counts.get(counter)!=1 or re.search(rf'&\s*\(*\s*{counter}\b', part.body):
            continue
        # Reject shadows and mixed word typedefs: use the actual declared pointer type.
        typ = None
        for declaration in re.finditer(rf'\b({WORD})\s+([^;{{}}()]+);', part.body):
            if all(re.search(rf'\*\s*{p}\b', declaration[2]) for p in (dest, src)):
                typ = declaration[1]
        if not typ:
            continue
        reverse = not bool(hit[3])
        groups.setdefault((typ, reverse), []).append((hit, dest, src, counter, count))
    for (typ, reverse), hits in groups.items():
        if not 2 <= len(hits) <= 12:
            continue
        helper = '__repair_copy_backward' if reverse else '__repair_copy_forward'
        if helper in source + header:
            continue
        body = '--s; --d; *d = *s;' if reverse else '*d++ = *s++;'
        definition = f'static __inline__ void {helper}({typ} *d, {typ} *s, int n) {{ for (; n != 0; --n) {{ {body} }} }}\n'
        edits = []
        sign = '-' if reverse else '+'
        for hit, dest, src, counter, count in hits:
            start = part.opening + 1 + hit.start()
            edits.append((start, part.opening + 1 + hit.end(), f'{{ {helper}({dest}, {src}, {count}); {dest} {sign}= {count}; {src} {sign}= {count}; {counter} = 0; }}'))
        # Insert after existing typedefs, before the requested definition (never before its types).
        from ..forks.csource import find
        edits.append((find(source, name)[0], find(source, name)[0], definition))
        out.append((FAMILIES[0], edit(source, edits)))

    # Two ordinary word stores describe a command record. Do not infer/modify shared struct layouts.
    # Keep the original pointer load and update before either payload expression is evaluated.
    command = re.compile(rf'\b({IDENT})\s*=\s*({IDENT}\s*->\s*{IDENT})\s*;\s*\2\s*=\s*\1\s*\+\s*2\s*;\s*\1\s*\[\s*0\s*\]\s*=\s*([^;{{}}]+);\s*\1\s*\[\s*1\s*\]\s*=\s*([^;{{}}]+);')
    edits = []
    tag = '__repair_command'
    if tag not in source + header and '__repair_command_ptr' not in source + header:
        for hit in command.finditer(part.body):
            p, owner, first, second = hit.groups()
            if p not in pointers or re.search(rf'\b{p}\b|\+\+|--|(?<![=!<>])=(?!=)|\b{IDENT}\s*\(', first + ' ' + second):
                continue
            # Member must have an explicitly known word-pointer type, not void*/unknown storage.
            from .capture_followup_repairs import member_type
            typ = member_type(part, header, owner)
            if not typ or not re.fullmatch(rf'{WORD}\s*\*', typ):
                continue
            text = (f'{p} = {owner}; {{ struct {tag} {{ unsigned int w0, w1; }}; '
                    f'struct {tag} *__repair_command_ptr = (struct {tag} *){p}; '
                    f'{owner} = ({typ})(__repair_command_ptr + 1); __repair_command_ptr->w0 = {first}; __repair_command_ptr->w1 = {second}; }}')
            edits.append((part.opening+1+hit.start(), part.opening+1+hit.end(), text))
        if edits:
            out.append((FAMILIES[1], edit(source, edits[:8])))
    return out


def copy_branches(source, name, header=''):
    """Extract repeated overlap-aware copy blocks when their induction locals are dead on exit."""
    from decomprepair.local_flow import analyze
    from decomprepair.pattern_repairs import close

    from ..forks.csource import find
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|restrict|__restrict)\b|^\s*#', part.clean, re.M):
        return []
    # Explicit, top-level locals only; no shadow/escaping pointer or induction storage.
    declarations = {}
    for decl in re.finditer(rf'\b({WORD}|s32|int)\s+([^;{{}}()]+);', part.body):
        if part.body[:decl.start()].count('{') != part.body[:decl.start()].count('}'):
            continue
        for item in decl[2].split(','):
            m = re.fullmatch(rf'\s*(\*?)\s*({IDENT})\s*', item)
            if m:
                var = m[2]
                if var in declarations:
                    declarations[var] = None
                else:
                    absolute = part.opening + 1 + decl.start(2) + decl[2].index(item) + m.start(2)
                    marker = re.compile(rf'(?P<name>{var})').match(part.clean, absolute)
                    declarations[var] = (decl[1], bool(m[1]), marker)
    counts = declaration_counts(part.body)
    declarations = {v:d for v,d in declarations.items() if d and not re.search(rf'&\s*\(*\s*{v}\b',part.body)
                    and counts.get(v)==1}
    flow = analyze(source, name, declarations=[d[2] for d in declarations.values()])
    if not flow:
        return []
    assignment = re.compile(rf'\s*({IDENT})\s*=\s*({IDENT}(?:\s*\+\s*[1-9][0-9]?)?)\s*;')
    loop = re.compile(rf'\s*for\s*\(\s*({IDENT})\s*=\s*([1-9][0-9]?)\s*;\s*\1\s*!=\s*0\s*;\s*--\s*\1\s*\)\s*{{(.*?)}}\s*',re.S)
    compact = lambda text: re.sub(r'[\s()]','',text)
    proposals = []
    for hit in re.finditer(r'\bif\s*\(', part.body):
        start = part.opening + 1 + hit.start()
        cond_end = close(part.clean, part.opening + hit.end())
        arm = re.match(r'\s*{',part.clean[cond_end:]) if cond_end else None
        if not arm: continue
        a = cond_end + arm.end()-1
        b = close(part.clean,a,'{','}')
        sibling = re.match(r'\s*else\s*{',part.clean[b:]) if b else None
        if not sibling: continue
        c = b+sibling.end()-1
        end = close(part.clean,c,'{','}')
        if not end: continue
        arms = []
        for left,right in ((a,b),(c,end)):
            text=part.clean[left+1:right-1]
            one=assignment.match(text)
            two=assignment.match(text,one.end()) if one else None
            body=loop.fullmatch(text[two.end():]) if two else None
            if not body:break
            arms.append(({one[1]:compact(one[2]),two[1]:compact(two[2])},body))
        if len(arms)!=2:continue
        reverse,forward=arms
        rloop,floop=reverse[1],forward[1]
        forward_store=re.fullmatch(rf'\s*\*\s*({IDENT})\+\+\s*=\s*\*\s*({IDENT})\+\+\s*;\s*',floop[3])
        if not forward_store or rloop[1]!=floop[1] or rloop[2]!=floop[2] or int(rloop[2])>32:continue
        d,s=forward_store.groups();n=floop[1];count=int(floop[2])
        if len({d,s,n})!=3 or any(v not in declarations for v in (d,s,n)):continue
        if not declarations[d][1] or not declarations[s][1] or declarations[n][1]:continue
        typ=declarations[d][0]
        if compact(typ)!=compact(declarations[s][0]) or not re.fullmatch(WORD,typ):continue
        if set(forward[0])!={d,s} or set(reverse[0])!={d,s}:continue
        dst,src=forward[0][d],forward[0][s]
        if not re.fullmatch(IDENT,dst) or not re.fullmatch(IDENT,src) or {dst,src}&{d,s,n}:continue
        se=reverse[0][s]
        if reverse[0][d]!=dst+'+'+str(count):continue
        backwards=compact(rloop[3])
        if backwards not in (s+'--;'+d+'--;*'+d+'=*'+s+';',d+'--;'+s+'--;*'+d+'=*'+s+';'):continue
        condition=compact(part.clean[part.opening+hit.end()+1:cond_end-1])
        # Casts here must be the same word type used by the original pointer comparisons.
        expected=compact(f'({typ}){dst}<({typ}){se}&({typ}){src}<({typ}){dst}')
        if condition!=expected:continue
        scratch={d,s,n}
        nodes={i for i,node in enumerate(flow.nodes) if start<=node.start<end}
        exits={j for i in nodes for j in flow.nodes[i].successors if j not in nodes}
        if any(scratch & flow.nodes[j].live for j in exits):continue
        proposals.append((start,end,typ,count,dst,src,se))
    groups={}
    for item in proposals:groups.setdefault((item[2],item[3]),[]).append(item)
    out=[]
    for (typ,count),items in groups.items():
        if not 2<=len(items)<=8:continue
        helper='__repair_overlap_copy'
        if helper in source+header:continue
        definition=(f'static __inline__ void {helper}({typ} *dst, {typ} *src, {typ} *se) {{ int i; '
                    f'if ((({typ})dst < ({typ})se) & (({typ})src < ({typ})dst)) {{ '
                    f'{typ} *s=se; {typ} *d=dst+{count}; for(i={count};i!=0;--i){{s--;d--;*d=*s;}} '
                    f'}} else {{ {typ} *s=src; {typ} *d=dst; for(i={count};i!=0;--i){{*d++=*s++;}} }} }}\n')
        edits=[(a,b,f'{helper}({dst}, {src}, {se});') for a,b,_,_,dst,src,se in items]
        at=find(source,name)[0];edits.append((at,at,definition))
        out.append((FAMILIES[0],edit(source,edits)))
    return out
