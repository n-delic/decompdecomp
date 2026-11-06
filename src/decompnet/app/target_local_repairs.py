"""Bounded, candidate-local hypotheses from call/alias and scheduling evidence.

No shared prototypes, symbol maps or object definitions are changed. These are source
hypotheses, not proofs of C equivalence; the ordinary destination judge must accept them.
"""
import re

from ..forks.csource import find
from .inferred_layout_repairs import accesses
from .integer_repair_support import IDENT, edit, integer, locals_for, view
from .pattern_repairs import close

FAMILIES = ('target_call_argument', 'known_address_alias', 'single_field_record',
            'record_access_shape', 'direct_mask_condition', 'split_call_result', 'call_result_reuse',
            'field_value_lifetime', 'target_field_load_order', 'target_local_bundle')
LIMIT = 32


def _part(source, name):
    part = view(source, name)
    if part:
        # A preceding local typedef can be included in find()'s definition span.
        signatures = list(re.finditer(r'\b'+re.escape(name)+r'\s*\([^;{}]*\)\s*\{', part.clean))
        if len(signatures)!=1:return None
        part.opening=signatures[0].end()-1
    return part


def call_arguments(source, name, asm, header=''):
    """A MIPS delay-slot load into a0 explicitly supplies the first argument.

    Only handle a unique direct no-argument call with a candidate-owned void prototype,
    fed by the preceding direct call's captured pointer result. Other ABIs stay advisory.
    """
    part = _part(source, name)
    if not part:
        return []
    insns = re.findall(r'/\*[^\n]*?\*/[ \t]*(\w+)[ \t]*([^\n]*)', asm or '')
    result = []
    previous = None
    for index, (op, operands) in enumerate(insns):
        if op != 'jal':
            continue
        callee = operands.strip()
        prior, previous = previous, callee
        if prior is None or index+1 >= len(insns):
            continue
        load, args = insns[index+1]
        hit = re.fullmatch(r'\$a0,\s*(0x[\dA-Fa-f]+|\d+)\(\$v0\)', args.strip())
        if load != 'lw' or not hit or integer(hit[1]) > 1024 or integer(hit[1]) % 4:
            continue
        proto = list(re.finditer(r'\b(?:extern\s+)?void\s+'+re.escape(callee)+r'\s*\(\s*void\s*\)\s*;', part.clean[:part.opening]))
        calls = list(re.finditer(r'\b'+re.escape(callee)+r'\s*\(\s*\)', part.body))
        if len(proto)!=1 or len(calls)!=1 or find(source,callee) or re.search(r'\b'+re.escape(callee)+r'\s*\(',header):
            continue
        call = calls[0]
        captures = list(re.finditer(r'\b('+IDENT+r')\s*=\s*'+re.escape(prior)+r'\s*\([^;{}]*\)\s*;',part.body[:call.start()]))
        if not captures:
            continue
        capture = captures[-1]
        tail = part.body[capture.end():call.start()]
        if re.search(r'\b'+re.escape(capture[1])+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)',tail+' '):
            continue
        # No intervening C calls: target/candidate call correspondence would be ambiguous.
        if any(x not in {'if', 'while', 'switch', 'sizeof'} for x in re.findall(r'\b('+IDENT+r')\s*\(', re.sub(r'\([^()]*\*[^()]*\)', '', tail))):
            continue
        offset=part.opening+1
        replacement=f'{callee}(*((int *)((char *){capture[1]} + {integer(hit[1])})))'
        result.append(('target_call_argument',edit(source,[(proto[0].start(),proto[0].end(),f'void {callee}(int);'),
                                                        (offset+call.start(),offset+call.end(),replacement)])))
    return result[:4]


def aliases(source, name):
    part=_part(source,name)
    if not part:
        return []
    result=[]
    # Resolve only address-bearing auto names and unscaled byte-array pointer assignments.
    for hit in re.finditer(r'\b('+IDENT+r')\s*=\s*(D_([\dA-Fa-f]{8}))\s*\+\s*(0x[\dA-Fa-f]+|\d+)\s*;',part.body):
        pointer,base=hit[1],hit[2]
        if not re.search(r'\bextern\s+(?:u8|unsigned char|char)\s+'+base+r'\s*\[\s*\]\s*;',part.clean):
            continue
        if len(re.findall(r'\b'+re.escape(pointer)+r'\s*=(?!=)',part.body))!=1:
            continue
        address=int(hit[3],16)+integer(hit[4])
        glob=f'D_{address:08X}'
        if not re.search(r'\bextern\s+(?:u32|unsigned int)\s+'+glob+r'\s*;',part.clean):
            continue
        tail=part.body[hit.end():]
        occurrences=list(re.finditer(r'\b'+glob+r'\b',tail))
        if not occurrences or re.search(r'\b'+glob+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)|&\s*'+glob+r'\b',tail):
            continue
        offset=part.opening+1+hit.end()
        candidate=edit(source,[(offset+m.start(),offset+m.end(),f'(*((unsigned int *){pointer}))') for m in occurrences])
        # Explicit identity helpers can hide the same memory expression from old compilers.
        for identity in re.finditer(r'\b(?:u32|unsigned int)\s+('+IDENT+r')\s*\(\s*(?:u32|unsigned int)\s+('+IDENT+r')\s*\)\s*\{\s*return\s*(?:\(\s*(?:u32|unsigned int)\s*\)\s*)?\2\s*;\s*\}',part.clean[:part.opening]):
            candidate_part=_part(candidate,name)
            changes=[]
            for call in re.finditer(r'\b'+re.escape(identity[1])+r'\s*\(',candidate_part.body):
                opening=candidate_part.opening+call.end()
                end=close(candidate,opening)
                if end:
                    changes.append((candidate_part.opening+1+call.start(),end,'((unsigned int)('+candidate[opening+1:end-1]+'))'))
            candidate=edit(candidate,changes)
        result.append(('known_address_alias',candidate))
    return result[:4]


def records(source, name, arch):
    part=_part(source,name)
    if not part or arch not in {'mips','ppc','x86','arm','thumb'}:
        return []
    hits=accesses(part.body)
    groups={}
    for a,b,base,typ,offset in hits:
        if not 0<=offset<=1024 or offset%4 or typ not in {'int','unsigned int','void *'}:
            continue
        groups.setdefault(offset,[]).append((a,b,base,typ))
    result=[]
    for offset,items in groups.items():
        # One layout for repeated equal fields, even if separate call results use separate locals.
        if len(items)<2:
            continue
        typ='void *' if all(x[3]=='void *' for x in items) else 'int'
        fresh=f'__repair_field_{offset:X}'
        if re.search(r'\b'+fresh+r'\b',part.clean):
            continue
        decl='typedef struct { '+(f'unsigned char pad[{offset}]; ' if offset else '')+f'{typ} value; }} {fresh};\n'
        changes=[(part.opening+1+a,part.opening+1+b,f'(({fresh} *){base})->value') for a,b,base,_ in items]
        changes.append((find(source,name)[0],find(source,name)[0],decl))
        result.append(('single_field_record',edit(source,changes)))
    # The inverse shape is useful when a typed record permits unwanted store motion.
    # Only our inferred layout naming convention, with a verified byte offset in its declaration.
    for record in re.finditer(r'typedef\s+struct\s*\{([^{}]+)\}\s*('+IDENT+r')\s*;',part.clean[:part.opening]):
        cursor=0;fields=[]
        for item in record[1].split(';'):
            item=item.strip()
            if not item:continue
            pad=re.fullmatch(r'unsigned char\s+'+IDENT+r'\[(\d+)\]',item)
            field=re.fullmatch(r'(unsigned short|unsigned char|int|unsigned int)\s+(field_([\dA-Fa-f]+))',item)
            if pad:cursor+=int(pad[1]);continue
            if not field:break
            size={'unsigned short':2,'unsigned char':1,'int':4,'unsigned int':4}[field[1]]
            cursor=(cursor+size-1)//size*size
            if cursor!=int(field[3],16):break
            fields.append((field[1],field[2],cursor));cursor+=size
        else:
            for typ,field,offset in fields:
                pattern=r'\(\('+re.escape(record[2])+r'\s*\*\)\s*('+IDENT+r')\)->'+field+r'\b'
                selected=list(re.finditer(pattern,part.body))
                if selected:
                    result.append(('record_access_shape',edit(source,[(part.opening+1+m.start(),part.opening+1+m.end(),f'*(({typ} *)((char *){m[1]} + {offset}))') for m in selected])))
    return result[:10]


def masks(source, name):
    part=_part(source,name)
    if not part:return []
    result=[]
    locals={d['name']:d for d in locals_for(part)}
    for hit in re.finditer(r'\b('+IDENT+r')\s*=\s*('+IDENT+r')\s*&\s*(0xFF|255)\s*;',part.body,re.I):
        dest,value=hit[1],hit[2]
        if dest not in locals or value not in locals or locals[value]['type'] not in {'u8','unsigned char'} or dest==value:
            continue
        # All definitions in this region must be the same mask. Stop at the next other definition.
        tail=part.body[hit.start():]
        stop=re.search(r'\b'+dest+r'\s*=(?!=)(?!\s*'+value+r'\s*&\s*(?:0xFF|255)\s*;)',tail)
        region=tail[:stop.start()] if stop else tail
        definitions=list(re.finditer(r'\b'+dest+r'\s*=\s*'+value+r'\s*&\s*(?:0xFF|255)\s*;',region,re.I))
        clean=re.sub(r'\b'+dest+r'\s*=\s*'+value+r'\s*&\s*(?:0xFF|255)\s*;','',region,flags=re.I)
        # Boolean uses only; do not rewrite arithmetic, address-taking or other lifetime semantics.
        uses=list(re.finditer(r'\b'+dest+r'\b',clean))
        conditions=list(re.finditer(r'\b'+dest+r'\s*!=\s*0',clean))
        if not uses or len(uses)!=len(conditions):continue
        offset=part.opening+1+hit.start()
        changes=[(offset+m.start(),offset+m.end(),'') for m in definitions]
        for m in re.finditer(r'\b'+dest+r'\s*!=\s*0',region):changes.append((offset+m.start(),offset+m.end(),value+' != 0'))
        result.append(('direct_mask_condition',edit(source,changes)))
    return list(dict.fromkeys(result))[:4]


def split_results(source, name):
    part=_part(source,name)
    if not part:return []
    result=[]
    for decl in locals_for(part):
        var=decl['name']
        assignments=list(re.finditer(r'\b'+re.escape(var)+r'\s*=\s*'+IDENT+r'\([^;{}]*\)\s*;',part.body))
        definitions=list(re.finditer(r'\b'+re.escape(var)+r'\s*=(?!=)',part.body))
        if not assignments or len(definitions)<2:continue
        for hit in assignments[-2:]:
            later=next((x for x in definitions if x.start()>hit.start()),None)
            end=later.start() if later else len(part.body)
            region=part.body[hit.start():end]
            uses=list(re.finditer(r'\b'+re.escape(var)+r'\b',region))
            region=region[:uses[-1].end()]
            if re.search(r'\b(?:goto|case|default)\b|\b'+var+r'\s*(?:\+\+|--|\+=|-=)',region):continue
            fresh='__repair_result_'+var
            if re.search(r'\b'+fresh+r'\b',part.clean):continue
            offset=part.opening+1+hit.start()
            changes=[(offset+m.start(),offset+m.end(),fresh) for m in re.finditer(r'\b'+var+r'\b',region)]
            changes.append((decl.end(),decl.end(),f'\n{decl["type"]} {fresh};'))
            result.append(('split_call_result',edit(source,changes)))
    return result[:8]


def value_lifetimes(source, name):
    part=_part(source,name)
    if not part:return []
    result=[]
    declarations={d['name']:d for d in locals_for(part)}
    # Reusing an earlier result slot is the complementary hypothesis to splitting one.
    captures=list(re.finditer(r'\b('+IDENT+r')\s*=\s*('+IDENT+r')\([^;{}]*\)\s*;',part.body))
    for current in captures:
        var,callee=current[1],current[2]
        if var not in declarations:continue
        later=part.body[current.end():]
        if re.search(r'\b'+re.escape(var)+r'\s*(?:=(?!=)|\+=|-=|\+\+|--)',later):continue
        uses=list(re.finditer(r'\b'+re.escape(var)+r'\b',later))
        if not uses:continue
        region=part.body[current.start():current.end()+uses[-1].end()]
        if re.search(r'\b(?:goto|case|default)\b',region):continue
        for previous in reversed(captures[:captures.index(current)]):
            old=previous[1]
            if previous[2]!=callee or old==var or old not in declarations or declarations[old]['type']!=declarations[var]['type']:continue
            if re.search(r'\b'+re.escape(old)+r'\b',part.body[current.start():]):continue
            offset=part.opening+1+current.start()
            result.append(('call_result_reuse',edit(source,[(offset+m.start(),offset+m.end(),old) for m in re.finditer(r'\b'+re.escape(var)+r'\b',region)])))
            break
    # Split a pointer-typed staging value when a newly inferred field is scalar. Its later,
    # unrelated pointer use keeps the original declaration; the field use gets a local int.
    for hit in re.finditer(r'\b('+IDENT+r')\s*=\s*\(\(('+IDENT+r')\s*\*\)('+IDENT+r')\)->value\s*;\s*('+IDENT+r')\s*=\s*\(void\s*\*\)\s*\1\s*;',part.body):
        if not re.search(r'\bvoid\s*\*\s*'+re.escape(hit[1])+r'\s*;',part.clean[part.opening:]):continue
        if not re.search(r'int value;\s*\}\s*'+re.escape(hit[2])+r'\s*;',part.clean[:part.opening]):continue
        fresh='__repair_value_'+hit[1]
        if re.search(r'\b'+fresh+r'\b',part.clean):continue
        block=source[part.opening+1+hit.start():part.opening+1+hit.end()]
        block=re.sub(r'\b'+re.escape(hit[1])+r'\b',fresh,block)
        result.append(('field_value_lifetime',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),block),
                                                          (part.opening+1,part.opening+1,'\nint '+fresh+';')])) )
    return result[:6]


def variants(source, name, arch, asm='', header='', *, bundle=True):
    part=_part(source,name)
    if not part or re.search(r'\b(?:volatile|restrict|asm|__asm__)\b',part.clean):return []
    from .field_load_repairs import variants as field_loads
    result=field_loads(source,name,arch,asm)+(call_arguments(source,name,asm,header) if arch=='mips' else [])+aliases(source,name)+records(source,name,arch)+masks(source,name)+split_results(source,name)+value_lifetimes(source,name)
    if bundle:
        # Bounded two-family combinations retain interactions without a Cartesian explosion.
        for family,candidate in result[:6]:
            other=next((s for f,s in variants(candidate,name,arch,asm,header,bundle=False) if f!=family),None)
            if other and other!=source:result.append(('target_local_bundle',other))
    groups={}
    for family,candidate in dict.fromkeys(result):
        groups.setdefault(family,[]).append(candidate)
    ordered=[]
    while len(ordered)<LIMIT and any(groups.values()):
        for family,items in groups.items():
            if items and len(ordered)<LIMIT:ordered.append((family,items.pop(0)))
    return ordered
