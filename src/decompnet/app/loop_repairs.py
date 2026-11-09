"""Bounded induction-variable alternatives, with no loop condition inversion or speculative trip count."""
import re

from ..forks.csource import _blank, find

IDENT=r'[A-Za-z_]\w*'
TYPE=r'(?:f32|f64|s32|u32|s16|u16|s8|u8|float|double|int|char)'


def variants(source,name):
    span=find(source,name)
    if not span:
        return []
    clean=_blank(source)
    opening=clean.find('{',*span)
    body=clean[opening:span[1]]
    if '#' in body or re.search(r'\bvolatile\b|__repair_cursor',body):
        return []
    # A narrowly recognized string-style latch: test the loaded byte before advancing the cursor.
    latch=re.search(r'\b('+IDENT+r')\s*:\s*('+IDENT+r')\s*\+=\s*1\s*;\s*'
                    r'if\s*\(\s*('+IDENT+r')\s*==\s*0\s*\)\s*return\s+NULL\s*;\s*'
                    r'\3\s*=\s*\*\s*\2\s*;\s*if\s*\(\s*\3\s*==\s*('+IDENT+r')\s*\)\s*return\s+\2\s*;\s*goto\s+\1\s*;',body)
    if latch and len(re.findall(r'\b'+re.escape(latch[1])+r'\b',body))==2 and re.fullmatch(r'\s*}\s*return\s+'+re.escape(latch[2])+r'\s*;\s*}',body[latch.end():]):
        label,pointer,value,needle=latch[1],latch[2],latch[3],latch[4]
        replacement=f'{label}: if({value}==0) return NULL; {pointer}++; {value}=*{pointer}; if({value}!={needle}) goto {label};'
        return [source[:opening+latch.start()]+replacement+source[opening+latch.end():]]
    if re.search(r'\b(?:goto|continue|break)\b',body):
        return []
    loops=list(re.finditer(r'\bdo\s*{',body))
    if len(loops)!=1:
        return []
    loop=loops[0];depth=1;end=loop.end()
    while end<len(body) and depth:
        depth+=(body[end]=='{')-(body[end]=='}');end+=1
    if depth:
        return []
    condition=re.match(r'\s*while\s*\(\s*('+IDENT+r')\s*<\s*('+IDENT+r')\s*\)\s*;',body[end:])
    if not condition:
        return []
    index=condition[1];inner=body[loop.end():end-1];prefix=body[:loop.start()]
    if not re.search(r'\b'+re.escape(index)+r'\s*=\s*0\s*;',prefix):
        return []
    if len(re.findall(r'\b'+re.escape(index)+r'\s*(?:\+\+|\+=|=)|\+\+\s*'+re.escape(index)+r'\b',inner))!=1:
        return []
    if not re.search(r'\b'+re.escape(index)+r'\s*(?:\+\+|\+=\s*1)\s*;',inner):
        return []
    result=[]

    def add(edits):
        text=source
        for a,b,new in sorted(edits,reverse=True):
            text=text[:opening+a]+new+text[opening+b:]
        if text not in result:
            result.append(text)

    for step in list(re.finditer(r'\b('+IDENT+r')\s*\+=\s*(\d+)\s*;',inner))[:3]:
        pointer=step[1];stride=int(step[2])
        if pointer==index or not 1<=stride<=64:
            continue
        decl=re.search(r'\b('+TYPE+r')\s*\*\s*'+re.escape(pointer)+r'\s*=\s*('+IDENT+r')\s*;',prefix)
        assign=None
        if not decl:
            decl=re.search(r'\b('+TYPE+r')\s*\*\s*'+re.escape(pointer)+r'\s*;',prefix)
            assign=re.search(r'\b'+re.escape(pointer)+r'\s*=\s*('+IDENT+r')\s*;',prefix)
        if not decl or (decl.lastindex==1 and not assign):
            continue
        base=assign[1] if assign else decl[2]
        accesses=list(re.finditer(r'\b'+re.escape(pointer)+r'\s*\[\s*(\d+)\s*\]',inner))
        covered=[(m.start(),m.end()) for m in accesses]+[(step.start(),step.end())]
        if not accesses or any(not any(a<=m.start()<b for a,b in covered) for m in re.finditer(r'\b'+re.escape(pointer)+r'\b',inner)):
            continue
        # No uses after the loop, escaped pointers, or hidden reassignments outside this one loop.
        after=end+condition.end()
        if re.search(r'\b'+re.escape(pointer)+r'\b',body[after:]):
            continue
        before_uses=len(re.findall(r'\b'+re.escape(pointer)+r'\b',prefix))
        if before_uses!=(2 if assign else 1):
            continue
        edits=[(decl.start(),decl.end(),''),(loop.end()+step.start(),loop.end()+step.end(),'')]
        if assign:
            edits.append((assign.start(),assign.end(),''))
        for m in accesses:
            edits.append((loop.end()+m.start(),loop.end()+m.end(),f'(({decl[1]} *){base})[{index}*{stride}+{m[1]}]'))
        add(edits)
    # Reverse direction: only explicit base[index*stride+constant] loads/stores with typed base.
    access=re.compile(r'\b('+IDENT+r')\s*\[\s*'+re.escape(index)+r'\s*\*\s*(\d+)\s*(?:\+\s*(\d+))?\s*\]')
    first=access.search(inner)
    if first and not prefix.rstrip().endswith(')'):
        base,stride=first[1],int(first[2])
        decl=re.search(r'\b('+TYPE+r')\s*\*\s*'+re.escape(base)+r'\b',clean[span[0]:opening]+prefix)
        hits=[m for m in access.finditer(inner) if m[1]==base and int(m[2])==stride]
        if decl and 1<=stride<=64 and hits and len(list(re.finditer(r'\b'+re.escape(base)+r'\b',inner)))==len(hits):
            # Place initialization before do, preserving the original entry guard.
            edits=[(1,1,f'\n{decl[1]} *__repair_cursor;\n'),(loop.start(),loop.start(),f'__repair_cursor = {base};\n'),
                   (end-1,end-1,f'\n__repair_cursor += {stride};\n')]
            edits += [(loop.end()+m.start(),loop.end()+m.end(),f'__repair_cursor[{m[3] or "0"}]') for m in hits]
            add(edits)
    if re.fullmatch(r'\s*}',body[end+condition.end():]) and re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(',clean[span[0]:opening]):
        for text in list(result):
            guard=re.search(r'\bif\s*\(\s*'+re.escape(condition[2])+r'\s*>\s*0\s*\)\s*do\b',text[opening:])
            if guard:
                a=opening+guard.start();b=opening+guard.end()
                result.append(text[:a]+f'if({condition[2]}<=0)return;do'+text[b:])
    return result[:4]
