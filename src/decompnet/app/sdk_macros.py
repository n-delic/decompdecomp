"""Keep required, unconditional local macro definitions with explicitly imported SDK C."""
import re


def context(text, source):
    definitions={}; ambiguous=set();depth=0;lines=iter(text.splitlines(True))
    for line in lines:
        directive=re.match(r'\s*#\s*(if|ifdef|ifndef|endif|define)\b(.*)',line)
        if not directive:
            continue
        kind=directive[1]
        if kind in {'if','ifdef','ifndef'}:
            depth+=1
        elif kind=='endif':
            depth=max(0,depth-1)
        elif kind=='define':
            block=line
            while block.rstrip('\r\n').endswith('\\'):
                extra=next(lines,'')
                if not extra:break
                block+=extra
            name=re.match(r'\s*('+r'[A-Za-z_]\w*'+')',directive[2])
            if not name:continue
            name=name[1]
            if depth:
                ambiguous.add(name)
                continue
            if name in definitions and definitions[name]!=block:
                ambiguous.add(name)
            definitions[name]=block
    needed=set(re.findall(r'\b[A-Za-z_]\w*\b',source)) & definitions.keys()
    for _ in range(16):
        extra=set(re.findall(r'\b[A-Za-z_]\w*\b',''.join(definitions[n] for n in needed))) & definitions.keys()
        if extra<=needed:break
        needed|=extra
    return ''.join(block for name,block in definitions.items() if name in needed and name not in ambiguous)
