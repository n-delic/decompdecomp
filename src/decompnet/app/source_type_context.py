"""Retain referenced typedefs and macros from the explicitly mapped file, without importing objects."""
import re

from ..forks.csource import _blank

IDENT=r'[A-Za-z_]\w*'


def referenced(text, source, header):
    clean=_blank(text);blocks=[]
    # Macro continuations stay intact. Never expand or execute reference code here.
    for m in re.finditer(r'(?m)^[ \t]*#[ \t]*define[ \t]+('+IDENT+r')\b(?:[^\n\\]|\\\n)*',clean):
        blocks.append((m[1],m.start(),m.end()))
    masked=re.sub(r'(?m)^[ \t]*#[^\n]*(?:\\\n[^\n]*)*',lambda m:' '*len(m[0]),clean)
    depth=0;start=0
    for i,c in enumerate(masked):
        depth+=(c=='{')-(c=='}')
        if c==';' and depth==0:
            part=masked[start:i+1]
            if re.match(r'\s*typedef\b',part):
                alias=re.search(r'\b('+IDENT+r')\s*;\s*$',part)
                if alias:
                    # Preprocessor lines were masked for parsing, but must not leak back
                    # through the original text slice (or overlap separately chosen macros).
                    first=start+len(part)-len(part.lstrip())
                    blocks.append((alias[1],first,i+1))
            if re.match(r'\s*(?:typedef\s+)?enum\b',part):
                enum=re.search(r'\benum(?:\s+'+IDENT+r')?\s*\{([^{}]*)\}',part,re.S)
                if enum:
                    first=start+len(part)-len(part.lstrip())
                    for item in enum[1].split(','):
                        name=re.match(r'\s*('+IDENT+r')\b',item)
                        if name:blocks.append((name[1],first,i+1))
            start=i+1
        elif c=='}' and depth==0 and ')' in masked[start:i]:
            start=i+1  # function definitions are not declarations
    used=set(re.findall(IDENT,_blank(source)));present=set(re.findall(IDENT,_blank(header)));chosen=set()
    for _ in range(16):
        before=(len(chosen),len(used))
        for name,a,b in blocks:
            if name in used and name not in present:
                chosen.add((a,b));used.update(re.findall(IDENT,clean[a:b]))
        for macro in re.finditer(r'(?m)^[ \t]*#\s*define\s+('+IDENT+r')\b([^\n]*)',header):
            if macro[1] in used:used.update(re.findall(IDENT,macro[2]))
        if (len(chosen),len(used))==before:break
    if not chosen:return ''
    # Keep original line positions, avoiding unrelated globals, prototypes and function bodies.
    out=[];at=0
    for a,b in sorted(chosen):
        out.append(re.sub(r'[^\n]',' ',text[at:a]));out.append(text[a:b]);at=b
    return '#line 1 "reference-types.c"\n'+''.join(out)+'\n'
