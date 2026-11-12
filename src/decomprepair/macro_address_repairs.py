"""Expose one proven byte-load macro address without expanding opaque macro effects."""
import re

from . import integer_abi
from .csource import _blank, find
from .integer_repair_support import IDENT, NUMBER, split_args
from .pattern_repairs import close

FAMILIES = ('macro_byte_address_order',)
LIMIT = 8
TYPES = {'unsignedchar':'unsigned char', 'signedchar':'signed char',
         'unsignedshort':'unsigned short', 'unsignedint':'unsigned int'}


def definitions(source, header, before=None):
    """Only source-owned, unconditional, single-use load expressions qualify."""
    rows = {}
    for hit in re.finditer(r'(?m)^[ \t]*#[^\n]*', _blank(source)):
        if before is not None and hit.start() >= before:
            return {}
        line = hit[0]
        macro = re.fullmatch(r'[ \t]*#[ \t]*define[ \t]+('+IDENT+r')\(('+IDENT+r'),\s*('+IDENT+r')\)\s+(.+)',line)
        if not macro or re.search(r'[#\\]',macro[4]):
            return {}
        name,base,offset,body = macro.groups()
        if name in rows or base == offset or re.search(r'(?m)^\s*#\s*define\s+'+re.escape(name)+r'\b',header):
            return {}
        if any(len(re.findall(r'\b'+re.escape(param)+r'\b',body))!=1 for param in (base,offset)):
            continue
        body = re.sub(r'\s+','',body)
        typ = r'(?P<type>(?:char|signedchar|unsignedchar|short|unsignedshort|int|unsignedint|[su](?:8|16|32))\*{1,2})'
        byte = r'(?P<byte>char|signedchar|unsignedchar|[su]8)'
        pattern = r'\(\*\('+typ+r'\)\(\('+byte+r'\*\)\('+re.escape(base)+r'\)\+\('+re.escape(offset)+r'\)\)\)'
        match = re.fullmatch(pattern,body)
        if match:
            cast = match['type'];stars=len(cast)-len(cast.rstrip('*'));plain=cast.rstrip('*')
            rows[name]=(TYPES.get(plain,plain)+'*'*stars,match['byte'],plain,stars)
    return rows


def declarations(text, name):
    pattern = (r'(?:^|[;{(,])\s*((?:(?:const|volatile|unsigned|signed|long|struct)\s+)*'+IDENT+r')'
               r'(?:\s*(\*+)\s*|\s+)'+re.escape(name)+r'\s*(?=[=;,\)\[])')
    return [(m[1],(m[2] or '')+('[]' if text[m.end():].lstrip().startswith('[') else ''))
            for m in re.finditer(pattern,text)]


def invocation(text, macros):
    match = re.fullmatch(r'\s*('+IDENT+r')\s*(\(.*\))\s*',text,re.S)
    if not match or match[1] not in macros or close(match[2],0)!=len(match[2]):
        return None
    text=match[2][1:-1]
    args=[text[a:b].strip() for a,b in split_args(text)]
    return (match[1],args) if len(args)==2 else None


def variants(source,name,arch,flags=(),header=''):
    if arch not in integer_abi.ARCHES or not set(flags)&{'-mgp32','-m32','-mabi=32'} or set(flags)&{'-m64','-mgp64','-mabi=64','-mabi=n32'}:
        return []
    clean=_blank(source);context=_blank(header)+'\n'+clean;span=find(source,name)
    if not span or re.search(r'\b(?:volatile|asm|__asm__|restrict|_Atomic)\b',clean):return []
    if re.search(r'(?m)^\s*#\s*define\s+(?:int|char|signed|unsigned)\b',context):return []
    macros=definitions(source,_blank(header),span[0])
    if not macros or integer_abi.scalar('int',context,arch)!=(32,True):return []
    opening=clean.find('{',*span);result=[]
    for hit in re.finditer(r'\b('+IDENT+r')\s*\(',clean[opening:span[1]]):
        macro=hit[1]
        if macro not in macros:continue
        start=opening+hit.start();end=close(clean,opening+hit.end()-1)
        if not end or end>span[1]:continue
        text=clean[opening+hit.end():end-1]
        args=[text[a:b].strip() for a,b in split_args(text)]
        if len(args)!=2 or not re.fullmatch(IDENT,args[1]):continue
        base=invocation(args[0],macros)
        if not base:continue
        inner,parts=base
        if macros[inner][3]!=2 or not re.fullmatch(IDENT,parts[0]) or not re.fullmatch(NUMBER,parts[1]):continue
        inner_type=macros[inner][2]
        if inner_type not in {'char','signedchar','unsignedchar','u8','s8'}:continue
        index_types=declarations(clean[span[0]:span[1]],args[1])
        base_types=declarations(clean[span[0]:span[1]],parts[0])
        if not index_types or any(stars or integer_abi.scalar(typ,context,arch) not in {(32,True),(32,False)} for typ,stars in index_types):continue
        if not base_types or any(stars!='*' or (typ!='char' and integer_abi.scalar(typ,context,arch) not in {(8,True),(8,False)}) for typ,stars in base_types):continue
        for typ in (macros[macro][1],macros[inner][1]):
            if typ!='char' and integer_abi.scalar(TYPES.get(typ,typ),context,arch) not in {(8,True),(8,False)}:break
        else:
            # Both operands are pure reads, still evaluated once. Leave all directives
            # and the inner load spelling intact; only the outer byte address changes.
            raw=source[opening+hit.end():end-1];raw_args=[raw[a:b].strip() for a,b in split_args(raw)]
            replacement='(*('+macros[macro][0]+')('+raw_args[1]+' + (int)('+raw_args[0]+')))'
            replacement+='\n'*max(0,source[start:end].count('\n')-replacement.count('\n'))
            candidate=source[:start]+replacement+source[end:]
            result.append((FAMILIES[0],candidate))
            if len(result)==LIMIT:break
    return list(dict.fromkeys(result))
