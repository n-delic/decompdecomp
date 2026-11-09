"""Bounded index, scalar-lifetime and alias-load hypotheses; only exact judges accept them."""
import re

from ..forks.csource import _blank, find

ID=r'[A-Za-z_]\w*'
NUM=r'(?:0x[0-9A-Fa-f]+|[0-9]+)'


def variants(source,name):
    span=find(source,name)
    if not span:
        return []
    clean=_blank(source);opening=clean.find('{',*span)
    if opening<0 or '#' in clean[opening:span[1]] or re.search(r'\bvolatile\b',clean):
        return []
    body=clean[opening:span[1]];out=[]
    def emit(family,edits):
        text=source
        for a,b,value in sorted(edits,reverse=True):
            text=text[:opening+a]+value+text[opening+b:]
        if text!=source and (family,text) not in out:
            out.append((family,text))
    arrays=set(re.findall(r'\bextern\s+(?:[A-Za-z_]\w*\s+)+('+ID+r')\s*\[\s*\]\s*;',clean[:opening]))
    indices={}
    for hit in re.finditer(r'\b('+ID+r')\s*\[\s*('+ID+r')\s*\]',body):
        if hit[1] in arrays:
            indices.setdefault((hit[1],hit[2]),[]).append(hit)
    for (_,index),hits in list(indices.items())[:2]:
        for typ in ('u16','u8'):
            builtin={'u16':'unsigned short','u8':'unsigned char'}[typ]
            emit('array_index_width',[(h.start(2),h.end(2),f'({builtin}){index}') for h in hits])
    # Split a simple scalar's later definition and uses until its next independent definition.
    for declaration in re.finditer(r'\b(f32|f64|float|double)\s+('+ID+r')\s*;',body):
        var=declaration[2]
        if re.search(r'&\s*'+re.escape(var)+r'\b',body):
            continue
        defs=[m for m in re.finditer(r'\b'+re.escape(var)+r'\s*=(?!=)([^;{}]*);',body[declaration.end():]) if not re.search(r'\b'+re.escape(var)+r'\b',m[1])]
        for i,m in enumerate(defs[1:3],1):
            start=declaration.end()+m.start();end=declaration.end()+defs[i+1].start() if i+1<len(defs) else len(body)
            if re.search(r'\b(?:for|while|goto|switch)\b',body[start:end]):
                continue
            fresh=f'__repair_{var}_{i}'
            if re.search(r'\b'+re.escape(fresh)+r'\b',clean):
                continue
            hits=list(re.finditer(r'\b'+re.escape(var)+r'\b',body[start:end]))
            edits=[(declaration.end(),declaration.end(),f'\n{declaration[1]} {fresh};')]
            edits += [(start+h.start(),start+h.end(),fresh) for h in hits]
            emit('split_scalar_lifetime',edits)
        if sum(f=='split_scalar_lifetime' for f,_ in out)>=4:
            break
    # Capture a repeated pointer field at an enclosing block's start, before stores in that block.
    load=re.compile(r'\*\s*\(\s*void\s*\*\s*\*\s*\)\s*\(\s*\(\s*(?:char|s8|u8)\s*\*\s*\)\s*('+ID+r')\s*\+\s*'+NUM+r'\s*\)')
    groups={}
    for hit in load.finditer(body):
        stack=[]
        for pos,ch in enumerate(body[:hit.start()]):
            if ch=='{':stack.append(pos)
            elif ch=='}' and stack:stack.pop()
        if stack:
            groups.setdefault((re.sub(r'\s+','',hit[0]),stack[-1]),[]).append(hit)
    for (_,block),hits in groups.items():
        if len(hits)<2:
            continue
        first=hits[0]
        prefix=body[block+1:first.start()]
        # A pending field macro is a load/store wrapper, not a call executed before the field load.
        prefix=re.sub(r'\bM2C_FIELD\s*\(\s*$','',prefix)
        if prefix.count('=')<2 or not re.search(r'\b'+re.escape(first[1])+r'\b',prefix):
            continue
        if re.search(r'\b'+ID+r'\s*\(|\b(?:for|while|goto|return)\b',prefix):
            continue
        fresh='__repair_early_ptr'
        if fresh in clean:
            continue
        expr=source[opening+first.start():opening+first.end()]
        for replacements in ([hits[-1]],hits):
            emit('pointer_before_store',[(block+1,block+1,f'\nvoid *{fresh} = {expr};\n')]+[(h.start(),h.end(),fresh) for h in replacements])
        if sum(f=='pointer_before_store' for f,_ in out)>=6:
            break
    return out
