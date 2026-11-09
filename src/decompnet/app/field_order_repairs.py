"""Bounded field store order proposals with declared record and conversion guards."""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view
from .pointer_pair_repairs import qualified_types

FAMILIES=('independent_field_store_order', 'equal_type_field_chain_order')


def variants(source,name,header=''):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b',part.clean):return []
    context=_blank(header)+'\n'+part.clean
    if re.search(r'::|\b(?:class|template|namespace)\b',context):return []
    qualified=qualified_types(context)
    if qualified & set(re.findall(IDENT,part.clean[find(source,name)[0]:part.end])):return []
    records={}
    for hit in re.finditer(rf'\btypedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*({IDENT})\s*;',context):
        body,typ=hit.groups()
        if re.search(r'\b(?:volatile|union)\b|:|#',body) or qualified & set(re.findall(IDENT,body)):continue
        fields={};valid=True
        plain=r'(?:unsigned\s+|signed\s+)?(?:char|short|int|long)|[su](?:8|16|32|64)|f32|f64|float|double|u?int(?:8|16|32|64)_t'
        for declaration in filter(str.strip,body.split(';')):
            extent=r'(?:0[xX][0-9a-fA-F]+|\d+)'
            suffix=rf'({IDENT})\s*(?:\[\s*({extent})\s*\])?\s*'
            field=(re.fullmatch(rf'\s*({plain})\s+'+suffix,declaration)
                   or re.fullmatch(rf'\s*((?:struct\s+)?{IDENT}\s*\*+)\s*'+suffix,declaration))
            if not field or (field[3] and int(field[3],16 if field[3].lower().startswith('0x') else 10)==0):valid=False;break
            fields[field[2]]=(' '.join(field[1].split()), bool(field[3]))
        if valid:records[typ]=fields
    objects={}
    signature=part.clean[find(source,name)[0]:part.opening]
    for typ,fields in records.items():
        # Parameters or plain local objects/pointers. Shadowed or escaped storage stays untouched.
        for hit in re.finditer(rf'\b{re.escape(typ)}\s*(\*?)\s*({IDENT})\s*(?=[,);])',signature+';'+part.body):
            pointer,var=hit.groups()
            if var in objects:objects[var]=None
            else:objects[var]=(pointer,fields)
    scalars={d['name'] for d in locals_for(part)}
    # Ordinary scalar/pointer parameters are stable local values too, unless qualified/escaped.
    for hit in re.finditer(rf'(?:^|,)\s*(?:int|s32|u32|short|s16|u16|u8|s8|char|void|{IDENT})\s*\**\s*({IDENT})\s*(?=,|\))',signature[signature.find('(')+1:]):
        if not re.search(rf'&\s*\(*\s*{re.escape(hit[1])}\b',part.body):scalars.add(hit[1])
    scalars={name for name in scalars if not re.search(rf'&\s*\(*\s*{re.escape(name)}\b',part.body)}
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',context,re.M))
    lhs=rf'({IDENT})(->|\.)({IDENT})(\[\s*({IDENT}|\d+)\s*\])?'
    store=rf'{lhs}\s*=(?!=)\s*({IDENT}|-?{NUMBER})\s*;'
    out=[]
    # Equal declared scalar types make both implicit conversions identical. Each field
    # address is pure and distinct, so reversing the chain still evaluates its RHS once.
    chain=rf'({IDENT})(->|\.)({IDENT})\s*=\s*\1\2({IDENT})\s*=\s*([^;{{}}]+);'
    for hit in re.finditer(chain,part.body):
        variable,access,left,right,rhs=hit.groups()
        obj=objects.get(variable)
        if not obj or access!=('->' if obj[0] else '.') or left==right:continue
        fields=obj[1]
        if left not in fields or right not in fields or fields[left]!=fields[right]:continue
        typ,array=fields[left]
        if array or '*' in typ:continue
        prefix=part.body[:hit.start()].rstrip()
        if prefix and prefix[-1:] not in {';', '{', '}'}:continue
        if re.search(rf'&\s*\(*\s*{re.escape(variable)}\b',part.body):continue
        if macros & set(re.findall(IDENT,hit[0])):continue
        # Restrict the RHS to arithmetic/local/member reads; leave further assignment,
        # comma expressions, calls, subscripts and conditional evaluation to other repairs.
        if re.search(r'(?<![<>=!])=(?!=)|(?:<<|>>)=|\+\+|--|[?,;\[\]]',rhs):continue
        if re.search(rf'\b{IDENT}\s*\(|\)\s*\(',rhs):continue
        base=part.opening+1
        out.append((FAMILIES[1],edit(source,[(base+hit.start(3),base+hit.end(3),right),
                                            (base+hit.start(4),base+hit.end(4),left)])))
        if len(out)==2:break
    for hit in re.finditer(rf'(?:^|(?<=[;{{}}]))\s*({store})\s*({store})',part.body):
        # Parse each complete store independently; preserve original comments/whitespace in the swap.
        stores=[re.fullmatch(store,hit[i]) for i in (1,8)]
        if not all(stores):continue
        a,b=stores
        obj=objects.get(a[1])
        if not obj or a[1]!=b[1] or a[2]!=b[2] or a[3]==b[3] or a[4]!=b[4]:continue
        if a[2]!=('->' if obj[0] else '.') or not {a[3],b[3]}<=set(obj[1]):continue
        if any(re.search(rf'&\s*\(*\s*{re.escape(v)}\b',part.body) for v in (a[1],)):continue
        if any(not re.fullmatch(rf'-?{NUMBER}',s[6]) and s[6] not in scalars for s in stores):continue
        if any(s[5] and not s[5].isdigit() and s[5] not in scalars for s in stores):continue
        if macros & set(re.findall(IDENT,hit[0])):continue
        start=part.opening+1+hit.start(1);end=part.opening+1+hit.end(8)
        left=source[start:part.opening+1+hit.end(1)]
        middle=source[part.opening+1+hit.end(1):part.opening+1+hit.start(8)]
        right=source[part.opening+1+hit.start(8):end]
        out.append((FAMILIES[0],edit(source,[(start,end,right+middle+left)])))
        if len(out)==4:break
    return out
