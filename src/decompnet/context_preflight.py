"""Reject only definite simple extern conflicts; unknown C still goes to the real regression judge."""
import re

from .forks.csource import _blank

TYPES={'s8':'signed char','u8':'unsigned char','s16':'short','u16':'unsigned short',
       's32':'int','u32':'unsigned int','f32':'float','f64':'double','signed int':'int',
       'short int':'short','signed short':'short','unsigned short int':'unsigned short'}
DECL=re.compile(r'^\s*extern\s+((?:(?:unsigned|signed)\s+)?(?:char|short(?:\s+int)?|int|float|double)|[su](?:8|16|32)|f(?:32|64))(?:\s*(\*)\s*|\s+)([A-Za-z_]\w*)\s*;\s*$')


def declarations(source):
    depth=conditional=0;result={};continued=False
    for line in _blank(source).splitlines():
        stripped=line.strip()
        if continued or (stripped.startswith('#') and stripped.endswith('\\')):
            continued=stripped.endswith('\\')
            continue
        if re.match(r'^#\s*(?:if|ifdef|ifndef)\b',stripped):
            conditional+=1
        elif re.match(r'^#\s*endif\b',stripped):
            conditional=max(0,conditional-1)
        if not depth and not conditional and not stripped.startswith('#'):
            m=DECL.fullmatch(line)
            if m:
                type_=' '.join(m[1].split());result[m[3]]=(TYPES.get(type_,type_),bool(m[2]))
        depth+=line.count('{')-line.count('}')
    return result


def conflicts(header,members):
    proposed=declarations(header);out=[]
    for member in members:
        own=declarations(member['matched_source'] or '')
        for name,type_ in proposed.items():
            if name in own and own[name]!=type_:
                out.append((member['name'],name))
    return out
