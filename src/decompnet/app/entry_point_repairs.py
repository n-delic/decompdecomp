"""Use a proven target address as an entry symbol instead of base-plus-offset C arithmetic."""
import re

from ..forks.csource import _blank, find
from ..rom.symbols import AUTO_NAME
from .addend_repairs import EXPR


def variants(source, name, asm):
    span=find(source,name)
    if not span:
        return []
    clean=_blank(source)
    lines=[re.sub(r'%(hi|lo)\(([^)]+)\)',
                  lambda m:'%'+m[1]+'('+re.sub(r'\s+','',m[2])+')',line.split('*/',1)[-1].strip())
           for line in (asm or '').splitlines()]
    evidence={}
    def number(text):
        return int(text,16 if text.lower().startswith('0x') else 8 if len(text)>1 and text.startswith('0') else 10)
    for i,line in enumerate(lines):
        high=re.fullmatch(r'lui\s+(\$\w+),\s*%hi\(([^)]+)\)',line)
        m=EXPR.search(line)
        if not high or not m or not m[4]:
            continue
        base=AUTO_NAME.fullmatch(m[2])
        if not base or not m[2].startswith('func_'):
            continue
        addend=number(m[4])*(-1 if m[3]=='-' else 1)
        address=int(base[1],16)+addend
        if addend<=0 or address%4 or not 0<address<=0xffffffff:
            continue
        for low in lines[i+1:i+13]:
            if re.fullmatch(r'addiu\s+'+re.escape(high[1])+r',\s*'+re.escape(high[1])+r',\s*%lo\('+re.escape(high[2])+r'\)',low):
                evidence.setdefault(m[2],set()).add((addend,address))
                break
            if re.match(r'(?!s[wdhb]\b)[a-z][a-z0-9.]*\s+'+re.escape(high[1])+r',',low):
                break
    result=[]
    for base,values in evidence.items():
        if len(values)!=1:
            continue
        addend,address=next(iter(values));symbol=f'func_{address:08X}'
        if re.search(r'\b'+symbol+r'\b',clean):
            continue  # an existing declaration may give it another type
        # Byte-pointer casts prove scale one. Keep comments, literals, and other arithmetic opaque.
        pattern=re.compile(r'\(\s*(?:u8|s8|char|unsigned\s+char|signed\s+char)\s*\*\s*\)\s*'
                           +re.escape(base)+r'\s*\+\s*(0[xX][0-9a-fA-F]+|[0-9]+)\b')
        hits=[m for m in pattern.finditer(clean) if number(m[1])==addend]
        for hit in hits[:2]:
            if not span[0] <= hit.start() < span[1]:
                line=clean[clean.rfind('\n',0,hit.start())+1:hit.start()]
                macro=re.match(r'\s*#\s*define\s+([A-Za-z_]\w*)\s+',line)
                if not macro or not re.search(r'\b'+re.escape(macro[1])+r'\b',clean[span[0]:span[1]]):
                    continue
            # This can occur in a macro before the body, e.g. an SDK cleanup entry. Reject
            # any additional stride or chained arithmetic: that is a different address.
            tail=clean[hit.end():].lstrip()
            if tail.startswith(('+','-','*','/','[','++','--')):
                continue
            candidate=source[:hit.start()]+'(u8*)'+symbol+source[hit.end():]
            candidate='extern void '+symbol+'(void);\n#line 1 "func.c"\n'+candidate
            result.append(candidate)
    return result[:2]
