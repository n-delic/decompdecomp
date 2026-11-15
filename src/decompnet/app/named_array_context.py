"""Bounded original-array copy hypotheses with an existing declaration and ROM-value proof."""
import re

from ..forks.csource import _blank, find


def variants(source, name, header, read, resolve, order='big'):
    span=find(source,name)
    if not span:return []
    clean=_blank(source);out=[]
    # Only plain 32-bit int arrays: no macro evaluation, inferred structs, padding or widths.
    declarations=list(re.finditer(r'\bextern\s+(?:const\s+)?int\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]\s*;',_blank(header)))[:64]
    local=re.compile(r'\bint\s+([A-Za-z_]\w*)\s*\[\s*(\d*)\s*\]\s*=\s*\{([^{}]*)\}\s*;')
    for m in local.finditer(clean,span[0],span[1]):
        tokens=[s.strip() for s in m[3].split(',') if s.strip()]
        if not 1<=len(tokens)<=32 or not all(re.fullmatch(r'-?(?:0[xX][0-9a-fA-F]+|\d+)',s) for s in tokens):continue
        count=int(m[2]) if m[2] else len(tokens)
        if count!=len(tokens):continue
        payload=b''.join(((int(t,16) if 'x' in t.lower() else int(t)) & 0xffffffff).to_bytes(4,order) for t in tokens)
        for decl in declarations:
            symbol,n=decl.groups()
            if int(n)!=count or symbol==m[1]:continue
            addr=resolve(symbol)
            if addr is None or read(addr,4*count)!=payload:continue
            # A local shadow or source-owned object is ambiguous; never rewrite shared declarations.
            if re.search(r'\b'+re.escape(symbol)+r'\b',clean):continue
            alias='PreparedArray_'+m[1]
            if re.search(r'\b'+alias+r'\b',_blank(header)+clean):continue
            tail=source[m.end():span[1]]
            from .source_preparation import rename
            tail=rename(tail,{m[1]:m[1]+'.values'})
            definition=f'typedef struct {{ int values[{count}]; }} {alias};\n'
            changed=source[:m.start()]+f'{alias} {m[1]} = *(const {alias} *){symbol};'+tail+source[span[1]:]
            out.append((definition+changed,header))
            if len(out)>=2:return out
    return out
