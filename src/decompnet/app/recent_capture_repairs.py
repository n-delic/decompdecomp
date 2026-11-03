"""Bounded hypotheses from captured repairs. Every result still needs the ordinary byte judge."""
import re

from .capture_followup_repairs import definitions
from .integer_repair_support import IDENT, edit, view

FAMILIES = ('shared_store_constant', 'nested_halfword_arithmetic', 'callee_array_extent',
            'overlay_callee_alias', 'argument_home_slot', 'untouched_argument_forwarding')


def variants(source, name, arch, asm='', header='', callees=''):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|restrict|union)\b',source) or re.search(r'^\s*#',part.body,re.M):return []
    # Keep declarations/directives outside the function intact. Never invent a local
    # whose spelling or scalar type is controlled by a macro.
    if re.search(r'^\s*#\s*define\s+(?:int|short|u16|__repair_\w+)\b',header+'\n'+source,re.M):return []
    result=[]
    def add(family, changes):
        text=edit(source,changes)
        if text!=source:result.append((family,text))
    base=part.opening+1
    # Share only literal RHS values; evaluation order and the stores themselves stay in place.
    groups={}
    for hit in re.finditer(r'(?<![=!<>])=\s*(-?(?:0[xX][0-9a-fA-F]+|[0-9]+))\s*;',part.body):
        value=int(hit[1],0) if not re.fullmatch(r'-?0[0-9]+',hit[1]) else int(hit[1],10)
        if -32768<=value<=32767 and value not in (0,1):groups.setdefault(hit[1],[]).append(hit)
    for value,hits in list(groups.items())[:3]:
        if len(hits)<2 or '__repair_constant' in source:continue
        for typ in ('int','short'):
            changes=[(part.opening+1,part.opening+1,f'\n{typ} __repair_constant = {value};\n')]
            changes += [(base+h.start(1),base+h.end(1),'__repair_constant') for h in hits[:8]]
            add(FAMILIES[0],changes)
        from .reference_stage_repairs import declaration_block
        block=declaration_block(source,name)
        if block:
            _,at,locals_=block
            prefix=re.match(r'(?:\s*'+IDENT+r'\s*=\s*(?:'+IDENT+r'|-?(?:0[xX][0-9a-fA-F]+|[0-9]+))\s*;){0,3}',part.clean[at:part.end-1])
            sites=[at]+[at+m.end() for m in re.finditer(';',prefix[0])]
            declared={n for n,_ in locals_}
            for site in sites[:3]:
                if site>at and not re.fullmatch(r'(?:\s*(?:'+ '|'.join(map(re.escape,declared))+r')\s*=\s*[^;]+;)*',part.clean[at:site]):continue
                changes=[(at,at,'\nint __repair_constant;'),(site,site,f'\n__repair_constant = {value};')]
                if site==at:changes=[(at,at,f'\nint __repair_constant;\n__repair_constant = {value};')]
                changes += [(base+h.start(1),base+h.end(1),'__repair_constant') for h in hits[:8]]
                add(FAMILIES[0],changes)

    # This is a scheduling hypothesis, not an algebraic equivalence claim across promotions/wraps.
    for hit in list(re.finditer(rf'\(\s*\(\s*(u16|unsigned\s+short)\s*\)\s*\(\s*({IDENT})\s*-\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)\s*\)\s*\+',part.body))[:3]:
        typ,var,value=hit.groups()
        if typ=='u16' and not re.search(r'\btypedef\s+unsigned\s+short\s+u16\s*;',header+'\n'+source):continue
        add(FAMILIES[1],[(base+hit.start(),base+hit.end(),f'({typ}){var} + ({typ})-{value} +')])
    known=definitions(header+'\n'+callees)
    # An escaped array can shrink only with a complete, simple callee write footprint,
    # bounded caller accesses and independent stack-frame evidence.
    if arch=='mips' and re.search(r'addiu\s+\$sp,\s*\$sp,\s*-',asm):
        for decl in re.finditer(rf'\b(int|s32)\s+({IDENT})\s*\[\s*(\d+)\s*\]\s*;',part.body):
            typ,var,old=decl.groups();uses=list(re.finditer(rf'\b{var}\b',part.body))
            calls=list(re.finditer(rf'\b({IDENT})\s*\(\s*{var}\s*\)',part.body))
            if len(calls)!=1 or calls[0][1] not in known:continue
            params,body=known[calls[0][1]]
            if len(params)!=1 or re.sub(r'\s','',params[0][0]) not in (typ+'*','int*' if typ=='s32' else typ+'*'):continue
            param=params[0][1]
            writes=list(re.finditer(rf'\b{param}\s*\[\s*(\d+)\s*\]\s*=(?!=)',body))
            indices={int(w[1]) for w in writes}
            if not indices or indices!=set(range(max(indices)+1)) or len(re.findall(rf'\b{param}\b',body))!=len(writes):continue
            if re.search(r'\b(?:for|while|goto)\b|\b'+IDENT+r'\s*\(',body):continue
            extent=max(indices)+1
            if not 1<=extent<int(old)<=32:continue
            indexed=list(re.finditer(rf'\b{var}\s*\[\s*(\d+)\s*\]',part.body))
            accesses=[h for h in indexed if not decl.start()<=h.start()<decl.end()]
            if len(uses)!=len(accesses)+2 or any(int(h[1])>=extent for h in accesses):continue
            # Require an actual stack-relative argument, not merely a function prologue.
            if not re.search(r'addiu\s+\$a[0-3],\s*\$sp,\s*0x[0-9a-f]+',asm):continue
            add(FAMILIES[2],[(base+decl.start(),base+decl.end(),f'{typ} {var}[{extent}];')])
    if arch!='mips':return result
    # Alias only to the exact symbolic call recorded in this target, never by VRAM alone.
    targets=set(re.findall(r'\bjal\s+(func_[0-9A-Fa-f]+_(?:ovl_\d+|overlay_\d+))\b',asm))
    for old in set(re.findall(r'\bfunc_[0-9A-Fa-f]+_(?:ovl_\d+|overlay_\d+)\b',part.body)):
        if old==name:continue
        address=old.split('_')[1]
        matches=[t for t in targets if t.split('_')[1].lower()==address.lower()]
        if len(matches)==1 and matches[0]!=old:
            add(FAMILIES[3],[(m.start(),m.end(),matches[0]) for m in re.finditer(r'\b'+re.escape(old)+r'\b',part.clean)])
    # Force an incoming argument's ABI home only when the target explicitly stores it there.
    signature=source[source.rfind(';',0,part.opening)+1:part.opening]
    params=re.search(r'\(([^()]*)\)\s*$',signature)
    if not params:return result
    args=[re.search(r'('+IDENT+r')\s*$',p) for p in params[1].split(',')]
    frame=re.search(r'addiu\s+\$sp,\s*\$sp,\s*-(0x[0-9a-fA-F]+|\d+)',asm)
    if frame:
        size=int(frame[1],0)
        for index,arg in enumerate(args[:4]):
            if not arg or not re.fullmatch(r'\s*(?:int|s32)\s+'+arg[1]+r'\s*',params[1].split(',')[index]) or re.search(r'&\s*'+arg[1]+r'\b',part.body):continue
            offsets=re.findall(rf'\bsw\s+\$a{index},\s*(0x[0-9a-fA-F]+|\d+)\(\$sp\)',asm)
            if any(int(offset,0)==size+4*index for offset in offsets):
                local='__repair_home'+str(index)
                if local in source:continue
                add(FAMILIES[4],[(part.opening+1,part.opening+1,f'\nint *{local} = &{arg[1]};\n'),
                    *[(base+m.start(),base+m.end(),'(*'+local+')') for m in re.finditer(r'\b'+arg[1]+r'\b',part.body)]])
    # A known callee arity plus unchanged incoming registers establishes forwarding.
    first=re.search(r'\bjal\s+('+IDENT+r')',asm)
    if first and first[1] in known:
        params,_=known[first[1]]
        before=asm[:first.start()]+'\n'+'\n'.join(asm[first.end():].splitlines()[1:2])
        if 1<=len(params)<=min(4,len(args)) and not re.search(r'\b(?:jal|jalr|b\w*)\s|\$a[0-3]\s*,',before):
            call=re.search(r'\b'+first[1]+r'\s*\(\s*\)',part.body)
            if call and all(args[:len(params)]):
                add(FAMILIES[5],[(base+call.start(),base+call.end(),first[1]+'('+', '.join(a[1] for a in args[:len(params)])+')')])
    return result[:24]
