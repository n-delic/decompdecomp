"""Small coordinated hypotheses from captured successes; the exact judge decides each candidate."""
import re

from .. import m2cfix
from ..forks.csource import _blank, find
from .pattern_repairs import close

ID = r'[A-Za-z_]\w*'
SIMPLE = rf'(?:&?{ID}(?:(?:->|\.){ID})*|(?:\([su](?:8|16|32)\))?(?:0x[0-9A-Fa-f]+|[0-9]+))'


def variants(source, name):
    span = find(source,name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{',*span)
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'\b(?:volatile|vu8|vu16|vu32)\b',clean):
        return []
    result=[]
    body=clean[opening:span[1]]
    def emit(family,edits):
        text=source
        for a,b,new in sorted(edits,reverse=True):
            text=text[:a]+new+text[b:]
        if text!=source and (family,text) not in result:
            result.append((family,text))
    # Coordinate a shortened call and its local prototype. No arbitrary RHS calls, increments,
    # variadics, function pointers or other callers of this declaration are rewritten.
    for call in re.finditer(rf'\b({ID})\s*\(',body):
        callee=call[1]
        if callee in {'if','while','for','switch','sizeof'}:
            continue
        end=close(body,call.end()-1)
        if not end:
            continue
        args=m2cfix._split_top_level(body[call.end():end-1])
        if len(args)<3 or len(args)>8 or len(re.findall(r'\b'+re.escape(callee)+r'\s*\(',body))!=1:
            continue
        proto=list(re.finditer(rf'\b(?:extern\s+)?(?:void|s32|u32|int|unsigned\s+int)\s+{re.escape(callee)}\s*\(([^;{{}}]*)\)\s*;',clean[:opening]))
        if len(proto)!=1:
            continue
        declaration=proto[0];types=m2cfix._split_top_level(declaration[1]) if declaration[1].strip() else []
        if types and (len(types)!=len(args) or '...' in declaration[1]):
            continue
        for count in range(2,min(3,len(args)-1)+1):
            if not all(re.fullmatch(SIMPLE,x.strip()) for x in args[-count:]):
                continue
            kept=args[:-count]
            edits=[(opening+call.end(),opening+end-1,', '.join(kept))]
            if types:
                edits.append((declaration.start(1),declaration.end(1),', '.join(types[:-count])))
            emit('call_arity',edits)
            if not types and all(re.fullmatch(r'(?:0x[0-9a-fA-F]+|\d+)',x.strip()) for x in kept):
                emit('call_arity',edits+[(declaration.start(1),declaration.end(1),', '.join(['int']*len(kept)))])
        if sum(f=='call_arity' for f,_ in result)>=4:
            break
    # Struct-typed pointers from a draft can accidentally scale a byte displacement. Coordinate
    # all literal additions for one declared pointer; never rewrite increments or dynamic indices.
    structs=set(re.findall(r'\btypedef\s+struct(?:\s+\w+)?\s*{[^{}]*}\s*('+ID+r')\s*;',clean))
    for typ in sorted(structs):
        pointers=set(re.findall(r'\b'+re.escape(typ)+rf'\s*\*\s*({ID})\b',clean[:span[1]]))
        all_hits=[]
        for pointer in sorted(pointers):
            hits=list(re.finditer(r'\b'+re.escape(pointer)+r'\s*\+\s*(0x[0-9A-Fa-f]+|\d+)(?![\w.])',body))
            all_hits += [(pointer,h) for h in hits]
        if not all_hits or len(all_hits)>12:
            continue
        for byte in ('unsigned char','char'):
            emit('struct_byte_offset',[(opening+h.start(),opening+h.end(),f'(({byte} *){pointer} + {h[1]})') for pointer,h in all_hits])
        if sum(f=='struct_byte_offset' for f,_ in result)>=4:
            break
    # Distribute one assignment/call over a flat switch whose arms only select simple arguments.
    # Each arm still runs the call once, after its argument assignments; no fallthrough is accepted.
    for switch in re.finditer(r'\bswitch\s*\(',body):
        cond_end=close(body,switch.end()-1)
        brace=body.find('{',cond_end or len(body))
        if not cond_end or brace<0 or body[cond_end:brace].strip():
            continue
        end=close(body,brace,'{','}')
        if not end:
            continue
        tail=re.match(r'\s*([^;{}]+?)\b('+ID+r')\s*\(([^;{}]*)\)\s*;',body[end:])
        if not tail or '=' not in tail[1]:
            continue
        args=m2cfix._split_top_level(tail[3])
        arms=list(re.finditer(r'\b(?:case\s+[^:;{}]+|default)\s*:',body[brace+1:end-1]))
        if not 2<=len(arms)<=8 or not any(a[0].startswith('default') for a in arms):
            continue
        edits=[];variables=set();valid=True
        for i,arm in enumerate(arms):
            start=brace+1+arm.end();stop=brace+1+(arms[i+1].start() if i+1<len(arms) else end-brace-2)
            arm_text=body[start:stop]
            parsed=re.fullmatch(r'\s*((?:'+ID+r'\s*=\s*&?'+ID+r'\s*;\s*|'+ID+r'\s*=\s*(?:0x[0-9A-Fa-f]+|\d+)\s*;\s*)+)break\s*;\s*',arm_text)
            if not parsed:
                valid=False;break
            values=dict(re.findall(r'('+ID+r')\s*=\s*(&?'+ID+r'|0x[0-9A-Fa-f]+|\d+)\s*;',parsed[1]))
            variables.update(values)
            if not all(v in [a.strip() for a in args] for v in values):
                valid=False;break
            replaced=[values.get(a.strip(),a.strip()) for a in args]
            call_text=tail[1]+tail[2]+'('+', '.join(replaced)+'); break;'
            edits.append((opening+start,opening+stop,'\n'+call_text+'\n'))
        # Replacing selected argument locals is safe only if they are not used later in this function.
        after=body[end+tail.end():]
        if valid and not any(re.search(r'\b'+re.escape(v)+r'\b',after) for v in variables):
            edits.append((opening+end,opening+end+tail.end(),''))
            emit('switch_call',edits)
        if sum(f=='switch_call' for f,_ in result)>=2:
            break
    return result
