"""Small type/lifetime and addition hypotheses isolated from repair suggestions."""
import re

from ..forks.csource import _blank, find


def close(text, start):
    depth=0
    for i in range(start,len(text)):
        depth+=(text[i]=='(')-(text[i]==')')
        if not depth:
            return i+1
    return None

IDENT=r'[A-Za-z_]\w*'


def variants(source,name):
    span=find(source,name)
    if not span:
        return []
    clean=_blank(source)
    definition=re.search(r'\b'+re.escape(name)+r'\s*\((?P<params>[^{};]*)\)\s*{',clean[span[0]:span[1]])
    if not definition:
        return []
    opening=span[0]+definition.end()-1
    parameter_start=span[0]+definition.start('params')
    body=clean[opening:span[1]]
    if '#' in body:
        return []
    result=[]
    def add(family,edits):
        text=source
        for a,b,new in sorted(edits,reverse=True):
            text=text[:a]+new+text[b:]
        if text!=source and text not in [s for _,s in result]:
            result.append((family,text))

    # Narrow only ordinary integer formals with source evidence of a byte/halfword mask/store.
    signature=clean[parameter_start:span[0]+definition.end()-1]
    for param in list(re.finditer(r'\b(s32|u32|int|unsigned\s+int)\s+('+IDENT+r')\s*(?=[,)])',signature))[:6]:
        variable=param[2]
        for bits in (16,8):
            mask='FFFF' if bits==16 else 'FF'
            evidence=re.search(r'\b'+re.escape(variable)+r'\s*&\s*0x'+mask+r'\b',body,re.I)
            evidence=evidence or re.search(r'\*\s*\(\s*[su]'+str(bits)+r'\s*\*\s*\)[^;{}=]+\s*=\s*'+re.escape(variable)+r'\s*;',body)
            if not evidence:
                continue
            for typ in ('u'+str(bits),'s'+str(bits)):
                add('parameter_width',[(parameter_start+param.start(1),parameter_start+param.end(1),typ)])
        if sum(f=='parameter_width' for f,_ in result)>=6:
            break

    # A pointer local reused only as integer bits for the rest of the function. Change no volatile
    # loads and move no evaluation: redirect that final assignment/casts into an unused scalar local.
    for m in list(re.finditer(r'\b('+IDENT+r')\s*=\s*\(\s*s32\s*\*\s*\)\s*('+IDENT+r'\s*\[\s*\d+\s*\])\s*;',body))[:4]:
        pointer=m[1]
        if not re.search(r'\bs32\s*\*\s*'+re.escape(pointer)+r'\s*;',body[:m.start()]):
            continue
        casts=list(re.finditer(r'\(\s*s32\s*\)\s*\b'+re.escape(pointer)+r'\b',body[m.end():]))
        refs=list(re.finditer(r'\b'+re.escape(pointer)+r'\b',body[m.end():]))
        if not casts or len(casts)!=len(refs):
            continue
        for decl in list(re.finditer(r'\bs32\s+('+IDENT+r')\s*;',body[:m.start()]))[:12]:
            scalar=decl[1]
            if len(re.findall(r'\b'+re.escape(scalar)+r'\b',body))!=1:
                continue
            edits=[(opening+m.start(),opening+m.end(),f'{scalar} = {source[opening+m.start(2):opening+m.end(2)]};')]
            edits += [(opening+m.end()+c.start(),opening+m.end()+c.end(),scalar) for c in casts]
            add('value_lifetime',edits)
            break

    if not re.search(r'\bvolatile\b',body):
        # Split an addition at depth zero, preserving the ordering of its two evaluations. Reject
        # calls/writes and a RHS that reads the destination (which splitting would overwrite early).
        for m in list(re.finditer(r'\b('+IDENT+r')\s*=\s*([^;{}]+);',body))[:20]:
            lhs,rhs=m[1],m[2];depth=0;plus=None
            for i,ch in enumerate(rhs):
                depth+=(ch in '([')-(ch in ')]')
                if ch=='+' and depth==0:
                    plus=i;break
            if plus is None or re.search(r'\b'+IDENT+r'\s*\(|\+\+|--|=',rhs):
                continue
            left,right=rhs[:plus].strip(),rhs[plus+1:].strip()
            if not left or not right or re.search(r'\b'+re.escape(lhs)+r'\b',right):
                continue
            pointer=re.search(r'\b(?:void|char|[su](?:8|16|32))\s*\*\s*'+re.escape(lhs)+r'\s*;',body[:m.start()])
            declared=pointer or re.search(r'\b(?:s32|u32|int|unsigned\s+int)\s+'+re.escape(lhs)+r'\s*;',body[:m.start()])
            if not declared:
                continue
            text=f'{lhs} = (char *)({left}); {lhs} += (s32)({right});' if pointer else f'{lhs} = {left}; {lhs} += {right};'
            add('sum_shape',[(opening+m.start(),opening+m.end(),text)])
            if sum(f=='sum_shape' for f,_ in result)>=4:
                break
        # A scaled offset plus a typed load used as a call argument: commute the two operands.
        for m in list(re.finditer(r'\b('+IDENT+r')\s*\+\s*(\*\s*\(\s*(?:s32|u32|int)\s*\*\s*\)\s*\()',body))[:4]:
            end=close(body,m.end()-1)
            if not end or re.search(r'\+\+|--|=|\b'+IDENT+r'\s*\(',body[m.end():end-1]):
                continue
            load=source[opening+m.start(2):opening+end]
            add('sum_shape',[(opening+m.start(),opening+end,load+' + '+m[1])])
    return result[:12]
