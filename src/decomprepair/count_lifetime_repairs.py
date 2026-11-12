"""Bounded count-phase and existing modular-guard hypotheses from an old MIPS compiler.

No source similarity accepts a result. Reads stay under their original guards and each
candidate must pass the ordinary compiler/judge. Never invent a call or shared type.
"""
import re

from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, view

FAMILIES = ('count_phase_lifetime', 'count_load_qualification', 'count_lifetime_bundle',
            'normalization_scope', 'modular_guard_width')
KEY = '0x13579bdfu'


def guard_gate():
    """Fail compilation on a different integer ABI or signed-short narrowing convention."""
    return ('typedef char __repair_narrow_contract[(sizeof(unsigned int)==4 && sizeof(short)==2 '
            '&& (short)0xffffu == -1 && (short)0x8000u == -32768)?1:-1];')


def guards(source, name, arch, header=''):
    """Retype only an existing, exactly recognized unsigned modular identity guard.

The captured input must be a local unsigned halfword most recently halved at the
shared test. It is therefore <=32767, including on the guard's zero-iteration path.
Do not narrow arbitrary arithmetic, observable stores or escaping temporaries.
"""
    part = view(source, name)
    if not part or arch != 'mips' or '__repair_narrow_contract' in source:
        return []
    declarations = {d['name']:d for d in locals_for(part)}
    pattern = (rf'{{\s*u32\s+({IDENT})\s*=\s*({IDENT})\s*;\s*u32\s+({IDENT})\s*=\s*\1\s*;'
               rf'\s*\3\s*\+=\s*{KEY}\s*;\s*\3\s*-=\s*{KEY}\s*;\s*if\s*\(\s*\3\s*!=\s*\1\s*\)\s*{{[^{{}}]*}}\s*}}')
    result = []
    for hit in re.finditer(pattern,part.body):
        origin = declarations.get(hit[2])
        if not origin or origin['type'] not in {'u16','unsigned short'}:
            continue
        if origin['type']=='u16' and not re.search(r'typedef\s+unsigned\s+short\s+u16\s*;',header+'\n'+source[:part.opening]):
            continue
        before = part.body[:hit.start()]
        # Only the exact shared-test spelling; neither casts nor a mere use prove a bound.
        halve = list(re.finditer(rf'\b{hit[2]}\s*=\s*\(\s*u32\s*\)\s*{hit[2]}\s*>>\s*1\s*;',before))
        if not halve:
            continue
        if block_path(part, part.opening+1+halve[-1].start()) != block_path(part, part.opening+1+hit.start()):
            continue
        prefix=before[:halve[-1].start()].rstrip()
        if prefix and prefix[-1] not in ';{}:':
            continue
        between = before[halve[-1].end():]
        if any(x not in {'if'} for x in re.findall(r'\b('+IDENT+r')\s*\(',between)):
            continue
        if re.search(r'(?<!:):(?!:)|\b(?:return|switch)\b',between):
            continue
        if re.search(rf'\b{hit[2]}\s*(?:[+*/&|^-]?=|\+\+|--)|&\s*{hit[2]}\b',between):
            continue
        # Identity locals must not be redeclared, address-taken or used outside this block.
        if any(re.search(rf'\b{x}\b',part.body[:hit.start()]+part.body[hit.end():]) for x in (hit[1],hit[3])):
            continue
        macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')',header+'\n'+source,re.M))
        if macros.intersection(re.findall(IDENT,hit[0]+between)):
            continue
        block=source[part.opening+1+hit.start():part.opening+1+hit.end()]
        block=block.replace('u32 '+hit[3], 'short '+hit[3],1)
        result.append(('modular_guard_width',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),'{'+guard_gate()+block[1:])])))
    return result[:2]


def variants(source, name, arch, header=''):
    part = view(source,name)
    if not part or arch != 'mips' or re.search(r'\b(?:asm|__asm__)\b',part.clean):
        return []
    result=guards(source,name,arch,header)
    declarations={d['name']:d for d in locals_for(part)}
    # Wrap one original statement, never hoist an access or wrap break/continue/goto.
    for hit in list(re.finditer(rf'\b({IDENT})\s*=\s*\(\s*s16\s*\)\s*\1\s*;',part.body))[:2]:
        if hit[1] not in declarations or '__repair_once' in source:
            continue
        start=part.opening+1+hit.start();prefix=part.clean[:start].rstrip()
        if prefix[-1:]not in ';{}':
            continue
        statement=source[start:part.opening+1+hit.end()]
        result.append(('normalization_scope',edit(source,[(start,part.opening+1+hit.end(),'{int __repair_once;for(__repair_once=1;__repair_once;__repair_once=0){'+statement+'}}')])))
    # Count load -> shared halving test, with all writes inside this phase known.
    for halve in re.finditer(rf'\b({IDENT})\s*=\s*\(\s*u32\s*\)\s*\1\s*>>\s*(1|{IDENT})\s*;',part.body):
        var=halve[1];decl=declarations.get(var)
        shift=halve[2]
        if shift != '1':
            shift_decl=declarations.get(shift)
            writes=list(re.finditer(rf'\b{shift}\s*=(?!=)\s*([^;{{}}]+);',part.body))
            if (not shift_decl or len(writes)!=1 or writes[0][1].strip()!='1'
                    or writes[0].start()>=halve.start()
                    or re.search(rf'&\s*{shift}\b|\b{shift}\s*(?:[+*/&|^-]=|\+\+|--)|(?:\+\+|--)\s*{shift}\b',part.body)):
                continue
        if not decl or decl['type'] not in {'s32','int','signed int'} or re.search(r'\btypedef\b',part.body):
            continue
        arm=re.match(rf'\s*if\s*\(\s*{IDENT}\s*<\s*{var}\s*\)\s*{{\s*goto\s+{IDENT}\s*;\s*}}',part.body[halve.end():])
        if not arm:
            continue
        stop=halve.end()+arm.end()
        assignments=list(re.finditer(rf'\b{var}\s*=\s*({IDENT})->({IDENT})\s*;',part.body[:halve.start()]))
        if not assignments:
            continue
        first=assignments[0];start=first.start();segment=part.body[start:stop]
        prefix=part.body[:start].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        # Resolve an actual unsigned16 field through one unambiguous pointer type.
        pointer,field=first[1],first[2]
        context=header+'\n'+source[:part.opening]
        macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')',header+'\n'+source,re.M))
        if macros.intersection(re.findall(IDENT,segment)):
            continue
        types=set(re.findall(rf'\b({IDENT})\s*\*\s*{pointer}\s*[;,)]',source[:part.opening]+part.body))
        if len(types)!=1:
            continue
        typ=next(iter(types))
        if re.search(rf'\bstruct\s+{typ}\s*\*\s*{pointer}\b',source[:part.opening]+part.body):
            continue
        records=list(re.finditer(rf'typedef\s+struct(?:\s+{IDENT})?\s*{{([^{{}}]*)}}\s*{typ}\s*;',context,re.S))
        if len(records)!=1 or not re.search(rf'\b(?:u16|unsigned\s+short)\s+{field}\s*;',records[0][1]):
            continue
        if re.search(r'\b(?:return|break|continue|sizeof|typeof|__typeof__|_Alignof|_Generic)\b',segment):
            continue
        targets=set(re.findall(r'\bgoto\s+('+IDENT+r')\s*;',segment))
        labels=set(re.findall(r'(?<![\w?:])('+IDENT+r')\s*:(?!:)',segment))
        external=part.body[:start]+part.body[stop:]
        incoming=set(re.findall(r'\bgoto\s+('+IDENT+r')\s*;',external))
        if (not targets<=labels or incoming.intersection(labels)
                or block_path(part,part.opening+1+start)!=block_path(part,part.opening+1+halve.start())
                or re.search(r'\b(?:case|default)\b',segment)):
            continue
        if 'u16' in records[0][1] and not re.search(r'typedef\s+unsigned\s+short\s+u16\s*;',context):
            continue
        loads=list(re.finditer(rf'\b{var}\s*=\s*{pointer}->{field}\s*;',segment))
        stripped=re.sub(rf'\b{var}\s*=\s*{pointer}->{field}\s*;','',segment)
        stripped=re.sub(rf'\b{var}\s*=\s*\(\s*u32\s*\)\s*{var}\s*>>\s*{shift}\s*;','',stripped)
        if (not loads or re.search(rf'&\s*{var}\b|\b{var}\s*(?:[+*/&|^-]?=|\+\+|--)|(?:\+\+|--)\s*{var}\b',stripped)
                or '__repair_phase_count' in source):
            continue
        fresh='__repair_phase_count';body=source[part.opening+1+start:part.opening+1+stop]
        for token in reversed(list(re.finditer(rf'\b{var}\b',segment))):
            if segment[:token.start()].rstrip().endswith(('.', '->')):
                continue
            body=body[:token.start()]+fresh+body[token.end():]
        if shift != '1':
            body=re.sub(rf'(\b{fresh}\s*=\s*\(\s*u32\s*\)\s*{fresh}\s*>>\s*){shift}\b',r'\g<1>1',body)
        # Accept arbitrary whitespace in the captured load without moving it.
        qualified=re.sub(rf'\b{fresh}\s*=\s*{pointer}->{field}\s*;',f'{fresh}=((volatile {typ}*){pointer})->{field};',body,count=1)
        restore=var+'='+fresh+';'
        edits=[(part.opening+1,part.opening+1,'\nunsigned short '+fresh+';'),(part.opening+1+start,part.opening+1+stop,body+restore)]
        result.append(('count_phase_lifetime',edit(source,edits)))
        edits[-1]=(part.opening+1+start,part.opening+1+stop,qualified+restore)
        result.append(('count_load_qualification',edit(source,edits)))
        # Only reuse an existing void(void) prototype, never introduce an unresolved helper.
        callees=[x for x in re.findall(rf'\bvoid\s+({IDENT})\s*\(\s*void\s*\)\s*;',source[:part.opening]) if x not in macros]
        if callees:
            for storage in ('u32','short'):
                if '__repair_guard_x' in source:
                    break
                guard=('{'+(guard_gate() if storage=='short' else '')+'u32 __repair_guard_x='+fresh+';'+storage+
                       ' __repair_guard_y=__repair_guard_x;__repair_guard_y+='+KEY+';__repair_guard_y-='+KEY+
                       ';if(__repair_guard_y!=__repair_guard_x){'+callees[0]+'();}}')
                edits[-1]=(part.opening+1+start,part.opening+1+stop,qualified+guard+restore)
                result.append(('count_lifetime_bundle',edit(source,edits)))
        break
    return result[:8]
